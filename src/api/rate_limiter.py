"""
令牌桶限流器
基于 Redis 实现的分布式限流
"""

import logging
import time
from typing import Optional

import redis
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import config

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """令牌桶限流器"""

    def __init__(
        self,
        redis_client: redis.Redis,
        capacity: int = 30,
        refill_rate: int = 30,
        window: int = 60
    ):
        """
        初始化令牌桶限流器

        Args:
            redis_client: Redis 客户端
            capacity: 桶容量（最大令牌数）
            refill_rate: 填充速率（每个窗口期填充的令牌数）
            window: 时间窗口（秒）
        """
        self.redis = redis_client
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.window = window

    def _get_key(self, identifier: str) -> str:
        """生成 Redis Key"""
        return f"rate_limit:{identifier}"

    def is_allowed(self, identifier: str) -> tuple[bool, Optional[int]]:
        """
        检查是否允许请求

        Args:
            identifier: 标识符（通常是 user_id 或 IP）

        Returns:
            (是否允许, 重试等待时间秒数)
        """
        key = self._get_key(identifier)
        now = int(time.time())

        try:
            # 使用 Lua 脚本保证原子性
            lua_script = """
            local key = KEYS[1]
            local capacity = tonumber(ARGV[1])
            local refill_rate = tonumber(ARGV[2])
            local window = tonumber(ARGV[3])
            local now = tonumber(ARGV[4])

            -- 获取当前令牌数和上次更新时间
            local tokens = tonumber(redis.call('HGET', key, 'tokens'))
            local last_refill = tonumber(redis.call('HGET', key, 'last_refill'))

            -- 初始化
            if tokens == nil then
                tokens = capacity
                last_refill = now
            end

            -- 计算应该填充的令牌数
            local time_passed = now - last_refill
            local tokens_to_add = math.floor(time_passed * refill_rate / window)

            if tokens_to_add > 0 then
                tokens = math.min(capacity, tokens + tokens_to_add)
                last_refill = now
            end

            -- 尝试消费一个令牌
            if tokens > 0 then
                tokens = tokens - 1
                redis.call('HSET', key, 'tokens', tokens)
                redis.call('HSET', key, 'last_refill', last_refill)
                redis.call('EXPIRE', key, window * 2)
                return {1, 0}  -- 允许，无需等待
            else
                -- 计算需要等待的时间
                local wait_time = math.ceil(window / refill_rate)
                return {0, wait_time}  -- 不允许，返回等待时间
            end
            """

            result = self.redis.eval(
                lua_script,
                1,
                key,
                self.capacity,
                self.refill_rate,
                self.window,
                now
            )

            allowed = bool(result[0])
            retry_after = int(result[1]) if not allowed else None

            if not allowed:
                logger.warning(f"限流触发: {identifier}, 需等待 {retry_after} 秒")

            return allowed, retry_after

        except Exception as e:
            logger.error(f"限流检查失败: {e}")
            # 失败时默认允许（避免 Redis 故障导致服务不可用）
            return True, None

    def reset(self, identifier: str):
        """重置限流计数"""
        key = self._get_key(identifier)
        try:
            self.redis.delete(key)
            logger.info(f"重置限流: {identifier}")
        except Exception as e:
            logger.error(f"重置限流失败: {e}")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件"""

    def __init__(self, app, rate_limiter: TokenBucketRateLimiter):
        super().__init__(app)
        self.rate_limiter = rate_limiter

    async def dispatch(self, request: Request, call_next):
        """处理请求"""
        # 跳过健康检查端点
        if request.url.path in ["/health", "/api/health", "/"]:
            return await call_next(request)

        # 获取标识符（优先使用 user_id，否则使用 IP）
        identifier = None

        # 尝试从 Token 中提取 user_id
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            try:
                from .auth import verify_token
                token = auth_header.split(" ")[1]
                token_data = verify_token(token)
                identifier = token_data.user_id
            except Exception:
                pass

        # 如果没有 user_id，使用客户端 IP
        if identifier is None:
            identifier = request.client.host

        # 检查限流
        allowed, retry_after = self.rate_limiter.is_allowed(identifier)

        if not allowed:
            logger.warning(f"请求被限流: {identifier}, path={request.url.path}")
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)} if retry_after else {}
            )

        # 继续处理请求
        response = await call_next(request)
        return response


def create_rate_limiter() -> TokenBucketRateLimiter:
    """
    创建限流器实例

    Returns:
        TokenBucketRateLimiter 实例
    """
    try:
        # 连接 Redis
        redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            password=config.REDIS_PASSWORD if config.REDIS_PASSWORD else None,
            db=config.REDIS_DB,
            decode_responses=True
        )
        redis_client.ping()

        # 创建限流器
        rate_limiter = TokenBucketRateLimiter(
            redis_client=redis_client,
            capacity=config.RATE_LIMIT_REQUESTS,
            refill_rate=config.RATE_LIMIT_REQUESTS,
            window=config.RATE_LIMIT_WINDOW
        )

        logger.info(f"限流器初始化成功: {config.RATE_LIMIT_REQUESTS} 请求/{config.RATE_LIMIT_WINDOW} 秒")
        return rate_limiter

    except Exception as e:
        logger.error(f"限流器初始化失败: {e}")
        raise