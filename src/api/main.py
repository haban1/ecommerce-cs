"""
FastAPI 应用入口
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from ..config import config
from .routes import router
from .skill_routes import router as skill_router
from .knowledge_routes import router as knowledge_router

# 配置日志
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE) if config.LOG_FILE else logging.NullHandler()
    ]
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("正在初始化应用...")

    try:
        # MCP Server 不再作为独立容器或本机常驻服务启动，
        # 由父进程在需要时按需拉起 stdio 子进程。
        logger.info("MCP Tool Manager 将在首次请求时按需拉起子进程")

        # 初始化 Agent Graph（延迟加载，首次请求时初始化）
        logger.info("Agent Graph 将在首次请求时初始化")

        logger.info("应用启动成功")

    except Exception as e:
        logger.error(f"应用初始化失败: {e}", exc_info=True)
        raise

    yield

    # 关闭时清理
    logger.info("正在清理资源...")
    try:
        # 如果有初始化过 tool_manager，则清理
        # tool_manager = await get_tool_manager()
        # await tool_manager.cleanup()
        logger.info("资源清理完成")
    except Exception as e:
        logger.error(f"资源清理失败: {e}")


# 创建 FastAPI 应用
app = FastAPI(
    title="电商智能客服系统",
    description="基于 MCP 协议 + LangGraph 的电商智能客服 Agent 系统",
    version="1.0.0",
    lifespan=lifespan
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router)
app.include_router(skill_router)
app.include_router(knowledge_router)


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "电商智能客服系统 API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health"
    }


@app.get("/health")
async def health():
    """简单健康检查"""
    return {"status": "ok"}


def main():
    """主函数"""
    logger.info(f"启动 FastAPI 服务: {config.API_HOST}:{config.API_PORT}")

    uvicorn.run(
        "src.api.main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        workers=1,  # 由于使用了全局状态，暂时使用单 worker
        log_level=config.LOG_LEVEL.lower(),
        reload=False
    )


if __name__ == "__main__":
    main()