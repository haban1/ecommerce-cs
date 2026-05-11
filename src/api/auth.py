"""
JWT 鉴权模块
使用 python-jose 实现 JWT 签发和验证
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

from ..config import config

logger = logging.getLogger(__name__)

# JWT 配置
SECRET_KEY = config.JWT_SECRET_KEY
ALGORITHM = config.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = config.JWT_EXPIRE_MINUTES

# HTTP Bearer 认证
security = HTTPBearer()


class TokenData(BaseModel):
    """Token 数据模型"""
    user_id: str
    exp: datetime


class User(BaseModel):
    """用户模型"""
    user_id: str
    username: Optional[str] = None
    email: Optional[str] = None


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    创建 JWT Access Token

    Args:
        data: 要编码的数据（必须包含 user_id）
        expires_delta: 过期时间增量

    Returns:
        JWT Token 字符串
    """
    to_encode = data.copy()

    # 设置过期时间
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})

    # 编码 JWT
    try:
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        logger.debug(f"创建 Token 成功: user_id={data.get('user_id')}")
        return encoded_jwt
    except Exception as e:
        logger.error(f"创建 Token 失败: {e}")
        raise


def verify_token(token: str) -> TokenData:
    """
    验证 JWT Token

    Args:
        token: JWT Token 字符串

    Returns:
        TokenData 对象

    Raises:
        HTTPException: Token 无效或过期
    """
    try:
        # 解码 JWT
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # 提取 user_id
        user_id: str = payload.get("user_id")
        if user_id is None:
            raise HTTPException(
                status_code=401,
                detail="Token 无效：缺少 user_id",
                headers={"WWW-Authenticate": "Bearer"}
            )

        # 提取过期时间
        exp = payload.get("exp")
        if exp is None:
            raise HTTPException(
                status_code=401,
                detail="Token 无效：缺少过期时间",
                headers={"WWW-Authenticate": "Bearer"}
            )

        token_data = TokenData(
            user_id=user_id,
            exp=datetime.fromtimestamp(exp)
        )

        logger.debug(f"Token 验证成功: user_id={user_id}")
        return token_data

    except JWTError as e:
        logger.warning(f"Token 验证失败: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except Exception as e:
        logger.error(f"Token 验证异常: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token 验证失败",
            headers={"WWW-Authenticate": "Bearer"}
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security)
) -> User:
    """
    获取当前用户（依赖注入）

    Args:
        credentials: HTTP Bearer 凭证

    Returns:
        User 对象

    Raises:
        HTTPException: 认证失败
    """
    token = credentials.credentials

    # 验证 Token
    token_data = verify_token(token)

    # 构建用户对象
    user = User(user_id=token_data.user_id)

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(lambda: Security(security))
) -> Optional[User]:
    """
    获取当前用户（可选，不强制要求认证）

    Args:
        credentials: HTTP Bearer 凭证

    Returns:
        User 对象或 None
    """
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


def generate_token_for_user(user_id: str, **extra_data) -> str:
    """
    为用户生成 Token（便捷函数）

    Args:
        user_id: 用户ID
        **extra_data: 额外数据

    Returns:
        JWT Token 字符串
    """
    token_data = {"user_id": user_id, **extra_data}
    return create_access_token(token_data)