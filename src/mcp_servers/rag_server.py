"""\
RAG 知识库 MCP Server（FastMCP 版）

提供产品手册、政策文档、FAQ 搜索等工具。
使用 ChromaDB 作为向量存储，Redis 缓存完整文档段落。
实现 Sentence Window Retrieval 两阶段检索。

说明：
- 使用 FastMCP（类似 `mcp-test/server.py`）以获得更稳定的 stdio 握手。
- 重依赖（检索器/索引/模型）保持懒加载，避免在握手阶段阻塞。
- 日志输出到 stderr，避免污染 stdio MCP 协议流。
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import chromadb
from chromadb.config import Settings
import redis

# 注意：不要在 import 时加载检索相关重依赖（torch/embedding/reranker/BM25 索引等）。
SentenceWindowRetriever = None  # type: ignore

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger(__name__)


def init_chromadb() -> chromadb.Client:
    """初始化 ChromaDB"""

    persist_dir = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
    os.makedirs(persist_dir, exist_ok=True)

    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(
            anonymized_telemetry=False,
            allow_reset=True,
        ),
    )
    logger.info("ChromaDB 初始化成功: %s", persist_dir)
    return client


def init_redis() -> Optional[redis.Redis]:
    """初始化 Redis 连接（失败则返回 None）。"""

    try:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", "")
        redis_db = int(os.getenv("REDIS_DB", "0"))

        # 关键：显式设置 socket 超时，避免在 Redis 不可用时长时间卡住
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password if redis_password else None,
            db=redis_db,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT_S", "1.5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_S", "2.0")),
        )
        client.ping()
        logger.info("Redis 连接成功: %s:%s", redis_host, redis_port)
        return client
    except Exception as e:
        logger.warning("Redis 连接失败，将降级为本地缓存: %s", e)
        return None


class RAGService:
    """RAG 检索服务"""

    def __init__(self, chroma_client: chromadb.Client, redis_client: redis.Redis):
        self.chroma = chroma_client
        self.redis = redis_client

        self._init_collections()

        # 为每个知识库创建 SentenceWindowRetriever
        self.product_retriever = SentenceWindowRetriever(
            self.product_manual_collection,
            redis_client,
            window_size=3,
        )
        self.policy_retriever = SentenceWindowRetriever(
            self.policy_collection,
            redis_client,
            window_size=3,
        )
        self.faq_retriever = SentenceWindowRetriever(
            self.faq_collection,
            redis_client,
            window_size=3,
        )

    def _init_collections(self) -> None:
        self.product_manual_collection = self.chroma.get_or_create_collection(
            name="product_manuals",
            metadata={"description": "产品手册和使用说明书"},
        )
        self.policy_collection = self.chroma.get_or_create_collection(
            name="policies",
            metadata={"description": "平台政策文档"},
        )
        self.faq_collection = self.chroma.get_or_create_collection(
            name="faqs",
            metadata={"description": "常见问题 FAQ"},
        )
        logger.info("ChromaDB Collections 初始化成功")

    def search_knowledge(
        self,
        query: str,
        knowledge_type: str,
        filter_field: Optional[str] = None,
        filter_value: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        retriever_map = {
            "product_manual": self.product_retriever,
            "policy": self.policy_retriever,
            "faq": self.faq_retriever,
        }

        if knowledge_type not in retriever_map:
            raise ValueError(f"不支持的知识类型: {knowledge_type}")

        retriever = retriever_map[knowledge_type]

        where_filter = None
        if filter_field and filter_value:
            where_filter = {filter_field: filter_value}

        results = retriever.sentence_window_retrieve(
            query=query,
            top_k=top_k,
            where=where_filter,
        )

        formatted: list[dict[str, Any]] = []
        for r in results:
            formatted.append(
                {
                    "chunk_id": r["chunk_id"],
                    "text": r["expanded_text"],
                    "original_text": r["original_text"],
                    "relevance_score": r["relevance_score"],
                    "metadata": r["metadata"],
                    "source_type": knowledge_type,
                    "window_range": r.get("window_range"),
                }
            )

        return formatted


mcp = FastMCP("rag-server")

chroma_client = None
redis_client = None
rag_service: Optional[RAGService] = None
_init_lock = asyncio.Lock()


async def ensure_initialized() -> None:
    global chroma_client, redis_client, rag_service

    if rag_service is not None:
        return

    async with _init_lock:
        if rag_service is not None:
            return

        chroma_client = init_chromadb()
        redis_client = init_redis()

        global SentenceWindowRetriever
        if SentenceWindowRetriever is None:
            from ..retrieval.sentence_window import SentenceWindowRetriever as _SentenceWindowRetriever

            SentenceWindowRetriever = _SentenceWindowRetriever

        rag_service = RAGService(chroma_client, redis_client)


@mcp.tool()
async def search_knowledge(
    query: str,
    knowledge_type: str,
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """通用知识检索工具。

    knowledge_type: product_manual / policy / faq

    降级策略：当向量库 / Redis / 检索链路不可用或异常时，返回空列表而不是抛异常，避免上层调用超时/崩溃。
    """

    try:
        await ensure_initialized()
        assert rag_service is not None

        # 关键：检索链路包含 BM25 构建 / embedding / rerank 等重操作，
        # 必须加内部超时，避免 MCP 调用方超时或请求堆积。
        tool_timeout_s = float(os.getenv("RAG_TOOL_TIMEOUT_S", "20"))

        return await asyncio.wait_for(
            asyncio.to_thread(
                rag_service.search_knowledge,
                query=query,
                knowledge_type=knowledge_type,
                filter_field=filter_field,
                filter_value=filter_value,
                top_k=top_k,
            ),
            timeout=tool_timeout_s,
        )
    except Exception as e:
        logger.error("search_knowledge 降级返回空结果: %s", e, exc_info=True)
        return []


if __name__ == "__main__":
    mcp.run()