"""
重排序模块
使用 BGE-Reranker 对检索结果进行精排
"""

import logging
from typing import List, Dict, Any, Optional

from ..models import get_ali_rerank

logger = logging.getLogger(__name__)


# 全局单例实例
def get_reranker():
    """获取 Reranker 单例"""
    return get_ali_rerank()