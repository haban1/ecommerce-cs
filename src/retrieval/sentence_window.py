"""
Sentence Window Retrieval 模块
两阶段检索：小粒度精确匹配 + 完整上下文扩展
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import chromadb
import redis

from ..config import config
from .hybrid_search import HybridSearcher, build_bm25_index_from_collection
from .reranker import get_reranker

logger = logging.getLogger(__name__)


class SentenceWindowRetriever:
    """Sentence Window Retrieval 实现"""

    def __init__(
        self,
        chroma_collection: chromadb.Collection,
        redis_client: Optional[redis.Redis],
        window_size: int = 3
    ):
        """
        初始化

        Args:
            chroma_collection: ChromaDB Collection（存储小粒度切片）
            redis_client: Redis 客户端（存储完整上下文），不可用时降级为进程内缓存
            window_size: 上下文窗口大小（前后各扩展的句子数）
        """
        self.collection = chroma_collection
        self.redis = redis_client
        self.window_size = window_size
        self._local_context_cache: Dict[str, str] = {}
        self._cache_dir = Path(config.CHROMA_PERSIST_DIR) / "sentence_window_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # 懒初始化：避免在 MCP 握手/初始化阶段阻塞太久
        self._bm25_index = None
        self._hybrid_searcher = None
        self._reranker = None

        logger.info("SentenceWindowRetriever 初始化完成（BM25/Reranker 懒加载）")

    def _cache_file_path(self, cache_key: str) -> Path:
        """为本地降级缓存生成稳定文件路径。"""
        safe_name = cache_key.replace(":", "__")
        return self._cache_dir / f"{safe_name}.json"

    def _write_local_cache(self, cache_key: str, payload: str) -> None:
        """写入进程内缓存，并尽量持久化到磁盘。"""
        self._local_context_cache[cache_key] = payload

        try:
            self._cache_file_path(cache_key).write_text(payload, encoding="utf-8")
        except Exception as cache_error:
            logger.warning("写入本地上下文缓存失败 %s: %s", cache_key, cache_error)

    def _read_local_cache(self, cache_key: str) -> Optional[str]:
        """优先从内存读取，本地文件作为跨进程降级缓存。"""
        cached = self._local_context_cache.get(cache_key)
        if cached:
            return cached

        cache_file = self._cache_file_path(cache_key)
        if not cache_file.exists():
            return None

        try:
            cached = cache_file.read_text(encoding="utf-8")
        except Exception as cache_error:
            logger.warning("读取本地上下文缓存失败 %s: %s", cache_key, cache_error)
            return None

        self._local_context_cache[cache_key] = cached
        return cached

    def _get_cached_payload(self, cache_key: str) -> Optional[str]:
        """优先使用 Redis，失败或缺失时回退到本地缓存。"""
        if self.redis is not None:
            try:
                cached = self.redis.get(cache_key)
                if cached:
                    self._local_context_cache[cache_key] = cached
                    return cached
            except Exception as redis_error:
                logger.warning("Redis 读取上下文失败 %s: %s，回退到本地缓存", cache_key, redis_error)

        return self._read_local_cache(cache_key)

    def _normalize_rerank_results(
        self,
        rerank_output: Any,
        source_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """兼容不同 reranker 输出格式，并映射回原始召回结果。"""
        if not rerank_output:
            return []

        if not isinstance(rerank_output, list):
            rerank_output = list(rerank_output)

        normalized_results: List[Dict[str, Any]] = []
        used_indexes = set()
        text_to_indexes: Dict[str, List[int]] = {}
        for idx, item in enumerate(source_results):
            text_to_indexes.setdefault(item.get("text", ""), []).append(idx)

        for item in rerank_output:
            source_index: Optional[int] = None
            score: Optional[float] = None
            text_hint: Optional[str] = None

            if isinstance(item, dict):
                source_index = item.get("index")
                if source_index is None:
                    source_index = item.get("document_index")
                score = item.get("relevance_score")
                if score is None:
                    score = item.get("score")
                text_hint = item.get("text")
                if text_hint is None:
                    text_hint = item.get("document")
            else:
                source_index = getattr(item, "index", None)
                if source_index is None:
                    source_index = getattr(item, "document_index", None)
                score = getattr(item, "relevance_score", None)
                if score is None:
                    score = getattr(item, "score", None)
                text_hint = getattr(item, "text", None)
                if text_hint is None:
                    text_hint = getattr(item, "document", None)

            if not isinstance(source_index, int) or not (0 <= source_index < len(source_results)):
                source_index = None

            if source_index is None and text_hint is not None:
                candidate_indexes = text_to_indexes.get(str(text_hint), [])
                source_index = next((idx for idx in candidate_indexes if idx not in used_indexes), None)

            if source_index is None:
                source_index = next(
                    (idx for idx in range(len(source_results)) if idx not in used_indexes),
                    None,
                )

            if source_index is None:
                break

            used_indexes.add(source_index)
            normalized = source_results[source_index].copy()
            base_score = normalized.get("rrf_score", normalized.get("score", 0.0))
            normalized["relevance_score"] = float(score) if score is not None else float(base_score)
            normalized_results.append(normalized)

            if len(normalized_results) >= top_k:
                break

        return normalized_results

    def _rerank_results(
        self,
        query: str,
        source_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """兼容不同 reranker 调用签名。"""
        assert self._reranker is not None

        documents = [result.get("text", "") for result in source_results]
        rerank_fn = getattr(self._reranker, "rerank", None)
        if rerank_fn is None:
            raise TypeError(f"当前 reranker 不支持 rerank 接口: {type(self._reranker)}")

        call_variants = [
            {"args": (), "kwargs": {"query": query, "documents": documents, "top_k": top_k}},
            {"args": (), "kwargs": {"query": query, "documents": documents, "top_n": top_k}},
            {"args": (query, documents), "kwargs": {"top_k": top_k}},
            {"args": (query, documents), "kwargs": {"top_n": top_k}},
            {"args": (query, documents, top_k), "kwargs": {}},
        ]

        last_error: Optional[Exception] = None
        for variant in call_variants:
            try:
                rerank_output = rerank_fn(*variant["args"], **variant["kwargs"])
                return self._normalize_rerank_results(rerank_output, source_results, top_k)
            except TypeError as error:
                last_error = error
                continue

        if last_error is not None:
            raise last_error

        raise RuntimeError("reranker 调用失败")

    def _ensure_components(self) -> None:
        """确保混合检索器与重排序器已初始化。"""

        if self._hybrid_searcher is None:
            logger.info("正在构建 BM25 索引...")
            self._bm25_index = build_bm25_index_from_collection(self.collection)
            self._hybrid_searcher = HybridSearcher(self.collection, self._bm25_index)

        if self._reranker is None:
            self._reranker = get_reranker()

    def store_full_context(
        self,
        chunk_id: str,
        full_document: str,
        sentences: List[str],
        chunk_index: int,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        存储完整文档上下文到 Redis

        Args:
            chunk_id: 文档块 ID
            full_document: 完整文档文本
            sentences: 文档的句子列表
            chunk_index: 当前 chunk 在句子列表中的索引
            metadata: 额外元数据
        """
        try:
            context_data = {
                'chunk_id': chunk_id,
                'full_document': full_document,
                'sentences': sentences,
                'chunk_index': chunk_index,
                'total_sentences': len(sentences),
                'metadata': metadata or {}
            }

            payload = json.dumps(context_data, ensure_ascii=False)
            cache_key = f"chunk:{chunk_id}"

            redis_written = False
            if self.redis is not None:
                try:
                    self.redis.setex(cache_key, 86400, payload)
                    redis_written = True
                except Exception as redis_error:
                    logger.warning("Redis 存储上下文失败 %s: %s，降级写入本地缓存", chunk_id, redis_error)

            if not redis_written:
                self._write_local_cache(cache_key, payload)

            meta_key = f"chunk_meta:{chunk_id}"
            meta_payload = json.dumps(
                {
                    'chunk_id': chunk_id,
                    'chunk_index': chunk_index,
                    'total_sentences': len(sentences),
                    'metadata': metadata or {}
                },
                ensure_ascii=False,
            )

            meta_written = False
            if self.redis is not None:
                try:
                    self.redis.setex(meta_key, 86400, meta_payload)
                    meta_written = True
                except Exception as redis_error:
                    logger.warning("Redis 存储上下文元数据失败 %s: %s，降级写入本地缓存", chunk_id, redis_error)

            if not meta_written:
                self._write_local_cache(meta_key, meta_payload)

            logger.debug(f"存储上下文成功: {chunk_id}")

        except Exception as e:
            logger.error(f"存储上下文失败 {chunk_id}: {e}")
            raise

    def get_expanded_context(
        self,
        chunk_id: str,
        window_size: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取扩展后的完整上下文

        Args:
            chunk_id: 文档块 ID
            window_size: 窗口大小，None 则使用默认值

        Returns:
            {
                'chunk_id': str,
                'original_text': str,  # 原始小粒度文本
                'expanded_text': str,  # 扩展后的完整上下文
                'window_range': {'start': int, 'end': int, 'total': int},
                'metadata': dict
            }
        """
        if window_size is None:
            window_size = self.window_size

        try:
            cache_key = f"chunk:{chunk_id}"
            cached = self._get_cached_payload(cache_key)
            if not cached:
                logger.warning(f"未找到上下文: {chunk_id}")
                return None

            context_data = json.loads(cached)
            chunk_index = context_data['chunk_index']
            sentences = context_data['sentences']
            total = context_data['total_sentences']

            start_idx = max(0, chunk_index - window_size)
            end_idx = min(total, chunk_index + window_size + 1)

            original_text = sentences[chunk_index] if chunk_index < total else ''
            expanded_text = ' '.join(sentences[start_idx:end_idx])

            return {
                'chunk_id': chunk_id,
                'original_text': original_text,
                'expanded_text': expanded_text,
                'window_range': {
                    'start': start_idx,
                    'end': end_idx,
                    'total': total
                },
                'metadata': context_data.get('metadata', {})
            }

        except Exception as e:
            logger.error(f"获取扩展上下文失败 {chunk_id}: {e}")
            raise

    def sentence_window_retrieve(
        self,
        query: str,
        top_k: int = 5,
        hybrid_top_k: Optional[int] = None,
        window_size: Optional[int] = None,
        where: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        两阶段 Sentence Window Retrieval

        Stage 1: 在小粒度切片上做混合检索 + 重排序
        Stage 2: 根据命中的 chunk_id 从 Redis 获取扩展上下文

        Args:
            query: 查询文本
            top_k: 最终返回结果数量
            hybrid_top_k: 混合检索返回数量（传给重排序），默认为 top_k * 4
            window_size: 上下文窗口大小，None 则使用默认值
            where: 元数据过滤条件

        Returns:
            [
                {
                    'chunk_id': str,
                    'original_text': str,
                    'expanded_text': str,
                    'relevance_score': float,
                    'window_range': dict,
                    'metadata': dict
                },
                ...
            ]
        """
        if hybrid_top_k is None:
            hybrid_top_k = top_k * 4

        if window_size is None:
            window_size = self.window_size

        try:
            # Stage 1: 混合检索
            self._ensure_components()
            assert self._hybrid_searcher is not None
            assert self._reranker is not None

            logger.debug(f"Stage 1: 混合检索，query={query}, top_k={hybrid_top_k}")
            hybrid_results = self._hybrid_searcher.hybrid_search(
                query=query,
                top_k=hybrid_top_k,
                where=where
            )

            if not hybrid_results:
                logger.warning("混合检索无结果")
                return []

            # Stage 1.5: 重排序
            logger.debug(f"Stage 1.5: 重排序，候选数={len(hybrid_results)}")
            reranked_results = self._rerank_results(query, hybrid_results, top_k)

            if not reranked_results:
                logger.warning("重排序后无结果")
                return []

            # Stage 2: 扩展上下文
            logger.debug(f"Stage 2: 扩展上下文，结果数={len(reranked_results)}")
            final_results = []

            for result in reranked_results:
                chunk_id = result['id']

                # 获取扩展上下文
                expanded = self.get_expanded_context(chunk_id, window_size)

                if expanded:
                    final_result = {
                        'chunk_id': chunk_id,
                        'original_text': expanded['original_text'],
                        'expanded_text': expanded['expanded_text'],
                        'relevance_score': result.get('relevance_score', result.get('score', 0.0)),
                        'window_range': expanded['window_range'],
                        'metadata': {
                            **result.get('metadata', {}),
                            **expanded.get('metadata', {})
                        }
                    }
                    final_results.append(final_result)
                else:
                    logger.warning(f"无法获取扩展上下文: {chunk_id}，使用原始文本")
                    final_results.append({
                        'chunk_id': chunk_id,
                        'original_text': result.get('text', ''),
                        'expanded_text': result.get('text', ''),
                        'relevance_score': result.get('relevance_score', result.get('score', 0.0)),
                        'window_range': None,
                        'metadata': result.get('metadata', {})
                    })

            logger.info(f"Sentence Window Retrieval 完成，返回 {len(final_results)} 个结果")
            return final_results

        except Exception as e:
            logger.error(f"Sentence Window Retrieval 失败: {e}")
            raise

    def batch_store_contexts(
        self,
        contexts: List[Dict[str, Any]]
    ):
        """
        批量存储上下文

        Args:
            contexts: [
                {
                    'chunk_id': str,
                    'full_document': str,
                    'sentences': List[str],
                    'chunk_index': int,
                    'metadata': dict
                },
                ...
            ]
        """
        for context in contexts:
            try:
                self.store_full_context(
                    chunk_id=context['chunk_id'],
                    full_document=context['full_document'],
                    sentences=context['sentences'],
                    chunk_index=context['chunk_index'],
                    metadata=context.get('metadata')
                )
            except Exception as e:
                logger.error(f"批量存储失败 {context.get('chunk_id')}: {e}")


def create_sentence_window_retriever(
    collection_name: str = "documents",
    window_size: int = 3
) -> SentenceWindowRetriever:
    """
    创建 SentenceWindowRetriever 实例

    Args:
        collection_name: ChromaDB Collection 名称
        window_size: 上下文窗口大小

    Returns:
        SentenceWindowRetriever 实例
    """
    import os
    import chromadb
    from chromadb.config import Settings

    os.makedirs(config.CHROMA_PERSIST_DIR, exist_ok=True)
    chroma_client = chromadb.PersistentClient(
        path=config.CHROMA_PERSIST_DIR,
        settings=Settings(anonymized_telemetry=False)
    )
    collection = chroma_client.get_or_create_collection(name=collection_name)

    redis_client = None
    try:
        redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            password=config.REDIS_PASSWORD if config.REDIS_PASSWORD else None,
            db=config.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=2.0,
        )
        redis_client.ping()
    except Exception as redis_error:
        logger.warning("SentenceWindowRetriever Redis 不可用，启用本地降级缓存: %s", redis_error)
        redis_client = None

    return SentenceWindowRetriever(
        chroma_collection=collection,
        redis_client=redis_client,
        window_size=window_size
    )