"""
订单查询 MCP Server（FastMCP 版）

提供订单查询、订单详情、退款状态查询等工具。
使用 SQLAlchemy 连接 MySQL 数据库。

说明：
- 使用 FastMCP（类似 `mcp-test/server.py`）以获得更稳定的 stdio 握手。
- 业务侧数据库/Redis 连接保持懒加载，避免在握手阶段阻塞。
- 日志输出到 stderr，避免污染 stdio MCP 协议流。
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from sqlalchemy import Column, DateTime, DECIMAL, Enum, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

import redis

# 加载 .env 文件
load_dotenv()

# 配置日志到 stderr（stdio MCP 协议使用 stdout 传 JSON-RPC）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger(__name__)

Base = declarative_base()


class Order(Base):
    """订单模型"""

    __tablename__ = "orders"

    id = Column(String(32), primary_key=True, comment="订单ID")
    user_id = Column(String(32), nullable=False, index=True, comment="用户ID")
    product_name = Column(String(255), nullable=False, index=True, comment="商品名称")
    product_id = Column(String(32), nullable=False, comment="商品ID")
    amount = Column(DECIMAL(10, 2), nullable=False, comment="订单金额")
    status = Column(
        Enum("paid", "shipped", "delivered", "refunding", "refunded", "cancelled", name="order_status"),
        nullable=False,
        index=True,
        comment="订单状态",
    )
    payment_method = Column(String(50), comment="支付方式")
    shipping_address = Column(Text, comment="收货地址")
    tracking_number = Column(String(100), comment="物流单号")
    created_at = Column(DateTime, nullable=False, index=True, comment="下单时间")
    paid_at = Column(DateTime, comment="支付时间")
    shipped_at = Column(DateTime, comment="发货时间")
    delivered_at = Column(DateTime, comment="签收时间")
    refund_amount = Column(DECIMAL(10, 2), comment="退款金额")
    refund_status = Column(
        Enum("pending", "processing", "completed", "rejected", name="refund_status"),
        comment="退款状态",
    )
    refund_reason = Column(Text, comment="退款原因")
    reject_reason = Column(Text, comment="拒绝退款原因")
    estimated_refund_date = Column(DateTime, comment="预计退款到账日期")


def init_database() -> sessionmaker:
    """初始化数据库连接"""

    mysql_host = os.getenv("MYSQL_HOST", "localhost")
    mysql_port = os.getenv("MYSQL_PORT", "3306")
    mysql_user = os.getenv("MYSQL_USER", "root")
    mysql_password = os.getenv("MYSQL_PASSWORD", "")
    mysql_database = os.getenv("MYSQL_DATABASE", "ecommerce_cs")

    db_url = (
        f"mysql+pymysql://{mysql_user}:{mysql_password}@{mysql_host}:{mysql_port}/{mysql_database}"
        "?charset=utf8mb4"
    )
    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    SessionLocal = sessionmaker(bind=engine)

    logger.info("数据库连接成功: %s:%s/%s", mysql_host, mysql_port, mysql_database)
    return SessionLocal


def init_redis() -> Optional[redis.Redis]:
    """初始化 Redis 连接（失败则返回 None）"""

    try:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", "")
        redis_db = int(os.getenv("REDIS_DB", "0"))

        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password if redis_password else None,
            db=redis_db,
            decode_responses=True,
        )
        client.ping()
        logger.info("Redis 连接成功: %s:%s", redis_host, redis_port)
        return client
    except Exception as e:
        logger.warning("Redis 连接失败，将不使用缓存: %s", e)
        return None


SessionLocal: Optional[sessionmaker] = None
redis_client: Optional[redis.Redis] = None
_init_lock = asyncio.Lock()


async def ensure_initialized() -> None:
    """按需初始化 DB/Redis（只做一次）"""

    global SessionLocal, redis_client

    if SessionLocal is not None:
        return

    async with _init_lock:
        if SessionLocal is not None:
            return

        SessionLocal = init_database()
        redis_client = init_redis()


def _extract_order_id_from_text(text: str) -> List[str]:
    """从文本中提取订单号"""

    patterns = [
        r"\b\d{16,20}\b",  # 纯数字订单号
        r"\b[A-Z]{2}\d{13,17}\b",  # 字母前缀+数字
        r"\b[A-Z]\d{14,18}\b",  # 单字母+数字
    ]

    order_ids: list[str] = []
    for pattern in patterns:
        order_ids.extend(re.findall(pattern, text))

    return list(set(order_ids))


mcp = FastMCP("order-server")


@mcp.tool()
async def search_orders(
    user_id: str,
    keyword: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """根据用户ID、关键词、时间范围查询历史订单。

    支持按商品名模糊匹配、按日期范围过滤、按订单状态筛选。

    Returns:
        订单列表（订单ID、商品名、金额、状态、下单时间、物流单号）。
    """

    await ensure_initialized()
    assert SessionLocal is not None

    db: Session = SessionLocal()
    try:
        query = db.query(Order).filter(Order.user_id == user_id)

        if keyword:
            query = query.filter(Order.product_name.like(f"%{keyword}%"))

        if start_date:
            query = query.filter(Order.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.filter(Order.created_at <= datetime.fromisoformat(end_date))

        if status:
            query = query.filter(Order.status == status)

        orders = query.order_by(Order.created_at.desc()).limit(limit).all()

        return [
            {
                "order_id": o.id,
                "product_name": o.product_name,
                "amount": float(o.amount),
                "status": o.status,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "tracking_number": o.tracking_number,
            }
            for o in orders
        ]
    finally:
        db.close()


@mcp.tool()
async def get_order_detail(order_id: str) -> Dict[str, Any]:
    """根据订单ID查询订单完整详情。

    包括商品信息、金额、支付方式、收货地址、物流单号、订单状态时间线。
    """

    await ensure_initialized()
    assert SessionLocal is not None

    db: Session = SessionLocal()
    try:
        # 缓存优先
        if redis_client:
            cache_key = f"order:detail:{order_id}"
            cached = redis_client.get(cache_key)
            if cached:
                import json

                logger.info("从缓存读取订单 %s", order_id)
                return json.loads(cached)

        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise ValueError(f"订单 {order_id} 不存在")

        detail = {
            "order_id": order.id,
            "user_id": order.user_id,
            "product_name": order.product_name,
            "product_id": order.product_id,
            "amount": float(order.amount),
            "status": order.status,
            "payment_method": order.payment_method,
            "shipping_address": order.shipping_address,
            "tracking_number": order.tracking_number,
            "timeline": {
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
                "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
            },
        }

        if redis_client:
            import json

            redis_client.setex(cache_key, 300, json.dumps(detail, ensure_ascii=False))

        return detail
    finally:
        db.close()


@mcp.tool()
async def get_refund_status(order_id: str) -> Dict[str, Any]:
    """查询某个订单的退款状态。"""

    await ensure_initialized()
    assert SessionLocal is not None

    db: Session = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise ValueError(f"订单 {order_id} 不存在")

        if order.status not in ["refunding", "refunded"]:
            return {"order_id": order_id, "has_refund": False, "message": "该订单未发起退款"}

        return {
            "order_id": order_id,
            "has_refund": True,
            "refund_amount": float(order.refund_amount) if order.refund_amount else 0.0,
            "refund_status": order.refund_status,
            "refund_reason": order.refund_reason,
            "reject_reason": order.reject_reason,
            "estimated_refund_date": order.estimated_refund_date.isoformat() if order.estimated_refund_date else None,
        }
    finally:
        db.close()


@mcp.tool()
def extract_order_id_from_text(text: str) -> Dict[str, Any]:
    """从用户输入文本中提取订单号。"""

    order_ids = _extract_order_id_from_text(text)
    return {"found_order_ids": order_ids, "count": len(order_ids)}


if __name__ == "__main__":
    mcp.run()