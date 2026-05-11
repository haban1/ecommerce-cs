"""
混合检索模块
Dense 检索（向量相似度）+ Sparse 检索（BM25）+ RRF 融合
"""

import logging
from typing import List, Dict, Any, Optional
import numpy as np
from rank_bm25 import BM25Okapi
import jieba
import chromadb

from ..config import config
from ..models import get_ali_embeddings

logger = logging.getLogger(__name__)


class BM25Index:
    """BM25 索引管理器"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.bm25: Optional[BM25Okapi] = None
        self.corpus_ids: List[str] = []
        self.corpus_texts: List[str] = []
        self.tokenized_corpus: List[List[str]] = []

    def build_index(self, documents: List[Dict[str, Any]]):
        """
        构建 BM25 索引

        Args:
            documents: [{'id': str, 'text': str, ...}, ...]
        """
        try:
            self.corpus_ids = [doc['id'] for doc in documents]
            self.corpus_texts = [doc['text'] for doc in documents]

            # 分词
            logger.info(f"正在对 {len(documents)} 个文档进行分词...")
            self.tokenized_corpus = [
                list(jieba.cut_for_search(text))
                for text in self.corpus_texts
            ]

            # 构建 BM25 索引
            self.bm25 = BM25Okapi(
                self.tokenized_corpus,
                k1=self.k1,
                b=self.b
            )

            logger.info(f"BM25 索引构建完成，共 {len(self.corpus_ids)} 个文档")

        except Exception as e:
            logger.error(f"构建 BM25 索引失败: {e}")
            raise

    def search(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """
        BM25 检索

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            [{'id': str, 'text': str, 'score': float, 'rank': int}, ...]
        """
        if self.bm25 is None:
            raise ValueError("BM25 索引未构建，请先调用 build_index()")

        try:
            # 查询分词
            tokenized_query = list(jieba.cut_for_search(query))

            # 计算 BM25 分数
            scores = self.bm25.get_scores(tokenized_query)

            # 排序并获取 Top K
            top_indices = np.argsort(scores)[::-1][:top_k]

            results = []
            for rank, idx in enumerate(top_indices):
                results.append({
                    'id': self.corpus_ids[idx],
                    'text': self.corpus_texts[idx],
                    'score': float(scores[idx]),
                    'rank': rank + 1
                })

            return results

        except Exception as e:
            logger.error(f"BM25 检索失败: {e}")
            raise


class HybridSearcher:
    """混合检索器"""

    def __init__(
        self,
        chroma_collection: chromadb.Collection,
        bm25_index: Optional[BM25Index] = None
    ):
        self.collection = chroma_collection
        self.bm25_index = bm25_index
        self.embedding_model = get_ali_embeddings()

    def _embed_query(self, query: str) -> List[float]:
        """兼容不同 embedding 实现的查询向量化。"""
        if hasattr(self.embedding_model, "embed_query"):
            return self.embedding_model.embed_query(query)

        if hasattr(self.embedding_model, "encode_single"):
            vector = self.embedding_model.encode_single(query)
            return vector.tolist() if hasattr(vector, "tolist") else vector

        if hasattr(self.embedding_model, "encode"):
            vectors = self.embedding_model.encode([query])
            if hasattr(vectors, "tolist"):
                return vectors.tolist()[0]
            return vectors[0]

        raise TypeError(
            f"不支持的 embedding_model 类型: {type(self.embedding_model)}，缺少 embed_query/encode_single/encode 方法"
        )

    def dense_search(
        self,
        query: str,
        top_k: int = 20,
        where: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        Dense 向量检索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            where: 元数据过滤条件

        Returns:
            [{'id': str, 'text': str, 'score': float, 'rank': int, 'metadata': dict}, ...]
        """
        try:
            # 查询向量化
            query_embedding = self._embed_query(query)

            # ChromaDB 检索
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where
            )

            # 格式化结果
            formatted = []
            if results['ids'] and results['ids'][0]:
                for rank, (doc_id, text, distance, metadata) in enumerate(zip(
                    results['ids'][0],
                    results['documents'][0],
                    results['distances'][0],
                    results['metadatas'][0]
                )):
                    # 距离转相似度分数（余弦距离 -> 余弦相似度）
                    score = 1 - distance
                    formatted.append({
                        'id': doc_id,
                        'text': text,
                        'score': float(score),
                        'rank': rank + 1,
                        'metadata': metadata
                    })

            return formatted

        except Exception as e:
            logger.error(f"Dense 检索失败: {e}")
            raise

    def sparse_search(
        self,
        query: str,
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Sparse BM25 检索

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            [{'id': str, 'text': str, 'score': float, 'rank': int}, ...]
        """
        if self.bm25_index is None:
            logger.warning("BM25 索引未初始化，跳过 Sparse 检索")
            return []

        return self.bm25_index.search(query, top_k)

    def reciprocal_rank_fusion(
        self,
        dense_results: List[Dict[str, Any]],
        sparse_results: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion (RRF) 融合

        RRF 公式: score(d) = Σ 1 / (k + rank(d))

        Args:
            dense_results: Dense 检索结果
            sparse_results: Sparse 检索结果
            k: RRF 参数，默认 60

        Returns:
            融合后的结果列表
        """
        # 构建文档 ID -> RRF 分数映射
        rrf_scores: Dict[str, float] = {}
        doc_data: Dict[str, Dict[str, Any]] = {}

        # 处理 Dense 结果
        for result in dense_results:
            doc_id = result['id']
            rank = result['rank']
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in doc_data:
                doc_data[doc_id] = result

        # 处理 Sparse 结果
        for result in sparse_results:
            doc_id = result['id']
            rank = result['rank']
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in doc_data:
                doc_data[doc_id] = result

        # 按 RRF 分数排序
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        # 构建最终结果
        fused_results = []
        for rank, doc_id in enumerate(sorted_ids):
            result = doc_data[doc_id].copy()
            result['rrf_score'] = rrf_scores[doc_id]
            result['final_rank'] = rank + 1
            fused_results.append(result)

        return fused_results

    def hybrid_search(
        self,
        query: str,
        top_k: int = 20,
        alpha: float = 0.5,
        where: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        混合检索：Dense + Sparse + RRF

        Args:
            query: 查询文本
            top_k: 最终返回结果数量
            alpha: Dense/Sparse 权重（0-1），暂未使用，RRF 自动平衡
            where: 元数据过滤条件

        Returns:
            融合后的 Top K 结果
        """
        try:
            # Dense 检索
            logger.debug(f"执行 Dense 检索: {query}")
            dense_results = self.dense_search(query, top_k=top_k * 2, where=where)

            # Sparse 检索
            logger.debug(f"执行 Sparse 检索: {query}")
            sparse_results = self.sparse_search(query, top_k=top_k * 2)

            # RRF 融合
            logger.debug("执行 RRF 融合")
            fused_results = self.reciprocal_rank_fusion(
                dense_results,
                sparse_results,
                k=config.RRF_K
            )

            # 返回 Top K
            return fused_results[:top_k]

        except Exception as e:
            logger.error(f"混合检索失败: {e}")
            raise


def build_bm25_index_from_collection(
    collection: chromadb.Collection
) -> BM25Index:
    """
    从 ChromaDB Collection 构建 BM25 索引

    Args:
        collection: ChromaDB Collection

    Returns:
        BM25Index 实例
    """
    try:
        # 获取所有文档
        results = collection.get(include=['documents', 'metadatas'])

        if not results['ids']:
            logger.warning("Collection 为空，无法构建 BM25 索引")
            return BM25Index()

        # 构建文档列表
        documents = [
            {'id': doc_id, 'text': text}
            for doc_id, text in zip(results['ids'], results['documents'])
        ]

        # 构建索引
        bm25_index = BM25Index(k1=config.BM25_K1, b=config.BM25_B)
        bm25_index.build_index(documents)

        return bm25_index

    except Exception as e:
        logger.error(f"构建 BM25 索引失败: {e}")
        raise