"""
全局配置模块
从环境变量读取配置
"""

import os
from typing import Optional
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


class Config:
    """全局配置类"""

    # ==================== LLM 配置 ====================
    LLM_MODEL_NAME: str = os.getenv('LLM_MODEL_NAME', 'Qwen/Qwen2.5-7B-Instruct')
    VLLM_API_BASE: str = os.getenv('VLLM_API_BASE', 'http://localhost:8000/v1')
    VLLM_API_KEY: Optional[str] = os.getenv('VLLM_API_KEY')

    # ==================== Embedding 模型 ====================
    EMBEDDING_MODEL_PATH: str = os.getenv('EMBEDDING_MODEL_PATH', 'BAAI/bge-large-zh-v1.5')
    RERANKER_MODEL_PATH: str = os.getenv('RERANKER_MODEL_PATH', 'BAAI/bge-reranker-large')

    # ==================== MySQL 配置 ====================
    MYSQL_HOST: str = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_PORT: int = int(os.getenv('MYSQL_PORT', '3306'))
    MYSQL_USER: str = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD: str = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_DATABASE: str = os.getenv('MYSQL_DATABASE', 'ecommerce_cs')

    @property
    def MYSQL_URL(self) -> str:
        """MySQL 连接 URL"""
        return f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"

    # ==================== Redis 配置 ====================
    REDIS_HOST: str = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_PASSWORD: Optional[str] = os.getenv('REDIS_PASSWORD')
    REDIS_DB: int = int(os.getenv('REDIS_DB', '0'))

    # ==================== JWT 配置 ====================
    JWT_SECRET_KEY: str = os.getenv('JWT_SECRET_KEY', 'your-super-secret-jwt-key-change-this-in-production')
    JWT_ALGORITHM: str = os.getenv('JWT_ALGORITHM', 'HS256')
    JWT_EXPIRE_MINUTES: int = int(os.getenv('JWT_EXPIRE_MINUTES', '1440'))

    # ==================== API 配置 ====================
    API_HOST: str = os.getenv('API_HOST', '0.0.0.0')
    API_PORT: int = int(os.getenv('API_PORT', '8080'))
    API_WORKERS: int = int(os.getenv('API_WORKERS', '4'))

    # ==================== 限流配置 ====================
    RATE_LIMIT_REQUESTS: int = int(os.getenv('RATE_LIMIT_REQUESTS', '100'))
    RATE_LIMIT_WINDOW: int = int(os.getenv('RATE_LIMIT_WINDOW', '60'))

    # ==================== 向量数据库配置 ====================
    CHROMA_PERSIST_DIR: str = os.getenv('CHROMA_PERSIST_DIR', './data/chroma_db')

    # ==================== 日志配置 ====================
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE: str = os.getenv('LOG_FILE', './logs/app.log')

    # ==================== MCP Server 配置 ====================
    ORDER_SERVER_PORT: int = int(os.getenv('ORDER_SERVER_PORT', '5001'))
    RAG_SERVER_PORT: int = int(os.getenv('RAG_SERVER_PORT', '5002'))

    # MCP 连接/初始化超时（秒）
    MCP_STDIO_CONNECT_TIMEOUT_S: float = float(os.getenv('MCP_STDIO_CONNECT_TIMEOUT_S', '15'))
    MCP_SESSION_INIT_TIMEOUT_S: float = float(os.getenv('MCP_SESSION_INIT_TIMEOUT_S', '30'))

    # ==================== RAG 检索配置 ====================
    CHUNK_SIZE: int = int(os.getenv('CHUNK_SIZE', '512'))
    CHUNK_OVERLAP: int = int(os.getenv('CHUNK_OVERLAP', '128'))
    SENTENCE_WINDOW_SIZE: int = int(os.getenv('SENTENCE_WINDOW_SIZE', '3'))
    HYBRID_SEARCH_TOP_K: int = int(os.getenv('HYBRID_SEARCH_TOP_K', '20'))
    RERANK_TOP_K: int = int(os.getenv('RERANK_TOP_K', '5'))
    BM25_K1: float = float(os.getenv('BM25_K1', '1.5'))
    BM25_B: float = float(os.getenv('BM25_B', '0.75'))
    RRF_K: int = int(os.getenv('RRF_K', '60'))


# 全局配置实例
config = Config()