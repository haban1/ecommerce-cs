"""
知识库管理 API 路由
提供文件上传、重建索引和统计查询接口。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from ..config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


KNOWLEDGE_COLLECTIONS: Dict[str, Dict[str, str]] = {
    "product_manual": {
        "collection": "product_manuals",
        "subdir": "product_manuals",
        "description": "产品手册和使用说明",
    },
    "policy": {
        "collection": "policies",
        "subdir": "policies",
        "description": "平台政策文档",
    },
    "faq": {
        "collection": "faqs",
        "subdir": "faqs",
        "description": "常见问题 FAQ",
    },
}


class KnowledgeUploadResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")
    filename: str = Field(..., description="文件名")
    knowledge_type: str = Field(..., description="知识类型")
    collection_name: str = Field(..., description="ChromaDB collection 名称")
    saved_path: str = Field(..., description="保存路径")
    chunk_count: int = Field(..., description="索引的 chunk 数量")
    timestamp: str = Field(..., description="时间戳")


class KnowledgeReindexResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")
    knowledge_type: str = Field(..., description="知识类型")
    collection_name: str = Field(..., description="ChromaDB collection 名称")
    source_dir: str = Field(..., description="重建源目录")
    results: Dict[str, int] = Field(..., description="重建结果")
    cleared_count: int = Field(..., description="清空前的 chunk 数量")
    timestamp: str = Field(..., description="时间戳")


class KnowledgeStatsResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    timestamp: str = Field(..., description="时间戳")
    collections: Dict[str, Dict[str, Any]] = Field(..., description="各知识库统计")


def _sanitize_filename(filename: str) -> str:
    stem = Path(filename).stem
    suffix = Path(filename).suffix.lower()
    safe_stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", stem).strip("._") or "uploaded_file"
    return f"{safe_stem}{suffix}"


def _get_knowledge_config(knowledge_type: str) -> Dict[str, str]:
    if knowledge_type not in KNOWLEDGE_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的知识类型: {knowledge_type}")
    return KNOWLEDGE_COLLECTIONS[knowledge_type]


def _get_source_dir(collection_subdir: str) -> Path:
    base_dir = Path(config.CHROMA_PERSIST_DIR).resolve().parent / "pdfs"
    source_dir = base_dir / collection_subdir
    source_dir.mkdir(parents=True, exist_ok=True)
    return source_dir


def _build_loader(collection_name: str):
    from ..retrieval.document_loader import PDFDocumentLoader

    return PDFDocumentLoader(collection_name=collection_name)


@router.post("/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge_file(
    file: UploadFile = File(...),
    knowledge_type: str = Form("product_manual"),
):
    """上传单个知识库文件并立即索引。"""

    try:
        config_item = _get_knowledge_config(knowledge_type)
        source_dir = _get_source_dir(config_item["subdir"])

        original_name = file.filename or "uploaded_file"
        safe_name = _sanitize_filename(original_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stored_name = f"{Path(safe_name).stem}_{timestamp}{Path(safe_name).suffix}"
        saved_path = source_dir / stored_name

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")

        saved_path.write_bytes(content)

        loader = _build_loader(config_item["collection"])
        suffix = saved_path.suffix.lower()
        if suffix == ".pdf":
            chunk_count = loader.add_document(str(saved_path))
        elif suffix in (".txt", ".md"):
            chunk_count = loader.add_text_document(str(saved_path))
        else:
            raise HTTPException(status_code=400, detail="仅支持 pdf / txt / md 文件")

        logger.info(
            "知识库文件上传成功: %s -> %s (%s, chunks=%s)",
            original_name,
            saved_path,
            knowledge_type,
            chunk_count,
        )

        return KnowledgeUploadResponse(
            success=True,
            message=f"文件已上传并索引成功，共写入 {chunk_count} 个 chunk",
            filename=original_name,
            knowledge_type=knowledge_type,
            collection_name=config_item["collection"],
            saved_path=str(saved_path),
            chunk_count=chunk_count,
            timestamp=datetime.now().isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("上传知识库文件失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"上传知识库文件失败: {str(e)}")


@router.post("/reindex", response_model=KnowledgeReindexResponse)
async def reindex_knowledge(
    knowledge_type: str = Form("product_manual"),
    clear_existing: bool = Form(True),
):
    """重建指定知识库目录的索引。"""

    try:
        config_item = _get_knowledge_config(knowledge_type)
        source_dir = _get_source_dir(config_item["subdir"])
        loader = _build_loader(config_item["collection"])

        cleared_count = 0
        if clear_existing:
            cleared_count = loader.clear_collection()

        results = loader.load_and_index(str(source_dir))

        logger.info(
            "知识库重建完成: %s, cleared=%s, files=%s",
            knowledge_type,
            cleared_count,
            len(results),
        )

        return KnowledgeReindexResponse(
            success=True,
            message=f"重建完成，共处理 {len(results)} 个文件",
            knowledge_type=knowledge_type,
            collection_name=config_item["collection"],
            source_dir=str(source_dir),
            results=results,
            cleared_count=cleared_count,
            timestamp=datetime.now().isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("重建知识库失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"重建知识库失败: {str(e)}")


@router.get("/stats", response_model=KnowledgeStatsResponse)
async def get_knowledge_stats():
    """获取各知识库统计信息。"""

    try:
        collections: Dict[str, Dict[str, Any]] = {}
        for knowledge_type, config_item in KNOWLEDGE_COLLECTIONS.items():
            loader = _build_loader(config_item["collection"])
            stats = loader.get_collection_stats()
            stats.update({
                "knowledge_type": knowledge_type,
                "source_dir": str(_get_source_dir(config_item["subdir"])),
                "description": config_item["description"],
            })
            collections[knowledge_type] = stats

        return KnowledgeStatsResponse(
            success=True,
            timestamp=datetime.now().isoformat(),
            collections=collections,
        )

    except Exception as e:
        logger.error("获取知识库统计失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取知识库统计失败: {str(e)}")