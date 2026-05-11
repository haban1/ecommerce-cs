"""
API 路由定义
提供对话、会话管理、健康检查等接口
"""

import logging
from typing import Dict, Any, List
from datetime import datetime
import json
from dataclasses import is_dataclass, asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage, BaseMessage

from ..agent.graph import get_agent_app
from ..agent.tools import get_tool_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# ==================== 请求/响应模型 ====================

class ChatRequest(BaseModel):
    """对话请求"""
    message: str = Field(..., description="用户消息")
    user_id: str = Field(..., description="用户ID")
    session_id: str = Field(..., description="会话ID")
    stream: bool = Field(False, description="是否流式输出")


class ChatResponse(BaseModel):
    """对话响应"""
    response: str = Field(..., description="助手回复")
    session_id: str = Field(..., description="会话ID")
    timestamp: str = Field(..., description="时间戳")


class ConversationMessage(BaseModel):
    """对话消息"""
    role: str = Field(..., description="角色：user/assistant")
    content: str = Field(..., description="消息内容")
    timestamp: str = Field(..., description="时间戳")


class ConversationHistory(BaseModel):
    """对话历史"""
    session_id: str
    messages: List[ConversationMessage]
    total_messages: int


class HealthStatus(BaseModel):
    """健康状态"""
    status: str = Field(..., description="overall/degraded/down")
    timestamp: str
    services: Dict[str, Dict[str, Any]]


# ==================== 会话存储（简单内存存储）====================

class ConversationStore:
    """会话存储"""

    def __init__(self):
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}

    def add_message(self, session_id: str, role: str, content: str):
        """添加消息"""
        if session_id not in self.sessions:
            self.sessions[session_id] = []

        self.sessions[session_id].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """获取历史"""
        return self.sessions.get(session_id, [])

    def clear_session(self, session_id: str):
        """清除会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def get_all_sessions(self) -> List[str]:
        """获取所有会话ID"""
        return list(self.sessions.keys())


# 全局会话存储
conversation_store = ConversationStore()


def _serialize_sse_payload(value: Any) -> Any:
    """将 LangChain / dataclass / 嵌套容器转换为可 JSON 序列化结构。"""

    if isinstance(value, BaseMessage):
        payload: Dict[str, Any] = {
            "type": value.__class__.__name__,
            "content": value.content,
            "additional_kwargs": _serialize_sse_payload(getattr(value, "additional_kwargs", {})),
        }
        if getattr(value, "name", None) is not None:
            payload["name"] = getattr(value, "name")
        if getattr(value, "id", None) is not None:
            payload["id"] = getattr(value, "id")
        if getattr(value, "tool_calls", None) is not None:
            payload["tool_calls"] = _serialize_sse_payload(getattr(value, "tool_calls"))
        if getattr(value, "response_metadata", None) is not None:
            payload["response_metadata"] = _serialize_sse_payload(getattr(value, "response_metadata"))
        if getattr(value, "tool_call_id", None) is not None:
            payload["tool_call_id"] = getattr(value, "tool_call_id")
        return payload

    if is_dataclass(value):
        return _serialize_sse_payload(asdict(value))

    if isinstance(value, dict):
        return {str(k): _serialize_sse_payload(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_serialize_sse_payload(item) for item in value]

    if isinstance(value, tuple):
        return [_serialize_sse_payload(item) for item in value]

    if isinstance(value, set):
        return [_serialize_sse_payload(item) for item in value]

    if isinstance(value, datetime):
        return value.isoformat()

    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _summarize_stream_event(event: Any) -> Any:
    """缩减流式事件，仅保留节点名、关键状态和最后一条消息。"""

    def _trim_text(value: Any, limit: int = 800) -> Any:
        if isinstance(value, str) and len(value) > limit:
            return value[:limit] + "...[truncated]"
        return value

    def _trim_message(message: Any) -> Any:
        if isinstance(message, BaseMessage):
            payload: Dict[str, Any] = {
                "type": message.__class__.__name__,
                "content": _trim_text(message.content),
            }
            for attr in ("name", "id", "tool_call_id"):
                attr_value = getattr(message, attr, None)
                if attr_value is not None:
                    payload[attr] = attr_value
            if getattr(message, "tool_calls", None) is not None:
                payload["tool_calls"] = _serialize_sse_payload(getattr(message, "tool_calls"))
            return payload

        return _serialize_sse_payload(message)

    if is_dataclass(event):
        event = asdict(event)

    if not isinstance(event, dict):
        return _serialize_sse_payload(event)

    summary: Dict[str, Any] = {}
    node_name = None

    if len(event) == 1:
        node_name, node_payload = next(iter(event.items()))
        summary["node"] = node_name
        event = node_payload if isinstance(node_payload, dict) else {"value": node_payload}

    for key in ("intent", "active_skill", "tool_call_count", "current_order_id", "final_response", "error"):
        if key in event and event[key] not in (None, "", [], {}):
            summary[key] = _trim_text(_serialize_sse_payload(event[key]))

    if "messages" in event and isinstance(event["messages"], list):
        messages = event["messages"]
        summary["messages_count"] = len(messages)
        if messages:
            summary["message"] = _trim_message(messages[-1])

    if "tool_results" in event and isinstance(event["tool_results"], list):
        tool_results = event["tool_results"]
        summary["tool_results_count"] = len(tool_results)
        if tool_results:
            summary["tool_result"] = _serialize_sse_payload(tool_results[-1])

    if not summary:
        summary = {
            str(k): _trim_text(_serialize_sse_payload(v))
            for k, v in event.items()
            if k not in {"messages", "tool_results"} and v not in (None, "", [], {})
        }
        if node_name:
            summary["node"] = node_name

    return summary


# ==================== API 路由 ====================

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    对话接口（非流式）

    Args:
        request: 对话请求

    Returns:
        对话响应
    """
    try:
        logger.info(f"收到对话请求: session={request.session_id}, user={request.user_id}")

        # 获取 Agent App
        app = await get_agent_app()

        # 构建初始状态
        initial_state = {
            "messages": [HumanMessage(content=request.message)],
            "user_id": request.user_id,
            "current_order_id": "",
            "retrieval_context": "",
            "tool_results": [],
            "final_response": "",
            "tool_call_count": 0,
            "intent": ""
        }

        # 执行 Agent
        result = await app.ainvoke(initial_state)

        # 提取最终回复
        final_response = result.get("final_response", "抱歉，我无法处理您的请求。")

        # 保存对话历史
        conversation_store.add_message(request.session_id, "user", request.message)
        conversation_store.add_message(request.session_id, "assistant", final_response)

        return ChatResponse(
            response=final_response,
            session_id=request.session_id,
            timestamp=datetime.now().isoformat()
        )

    except Exception as e:
        logger.error(f"对话处理失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"对话处理失败: {str(e)}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    对话接口（流式）

    Args:
        request: 对话请求

    Returns:
        SSE 流式响应
    """
    try:
        logger.info(f"收到流式对话请求: session={request.session_id}, user={request.user_id}")

        # 获取 Agent App
        app = await get_agent_app()

        # 构建初始状态
        initial_state = {
            "messages": [HumanMessage(content=request.message)],
            "user_id": request.user_id,
            "current_order_id": "",
            "retrieval_context": "",
            "tool_results": [],
            "final_response": "",
            "tool_call_count": 0,
            "intent": ""
        }

        async def event_generator():
            """SSE 事件生成器"""
            try:
                # 流式执行 Agent
                async for event in app.astream(initial_state):
                    # 发送中间状态
                    safe_event = _summarize_stream_event(event)
                    yield f"data: {json.dumps(safe_event, ensure_ascii=False)}\n\n"

                # 发送完成信号
                yield "data: [DONE]\n\n"

            except Exception as e:
                logger.error(f"流式处理失败: {e}")
                error_data = {"error": str(e)}
                yield f"data: {json.dumps(_serialize_sse_payload(error_data), ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream"
        )

    except Exception as e:
        logger.error(f"流式对话处理失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"流式对话处理失败: {str(e)}")


@router.get("/conversations/{session_id}", response_model=ConversationHistory)
async def get_conversation_history(session_id: str):
    """
    获取对话历史

    Args:
        session_id: 会话ID

    Returns:
        对话历史
    """
    try:
        history = conversation_store.get_history(session_id)

        messages = [
            ConversationMessage(
                role=msg["role"],
                content=msg["content"],
                timestamp=msg["timestamp"]
            )
            for msg in history
        ]

        return ConversationHistory(
            session_id=session_id,
            messages=messages,
            total_messages=len(messages)
        )

    except Exception as e:
        logger.error(f"获取对话历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取对话历史失败: {str(e)}")


@router.delete("/conversations/{session_id}")
async def clear_conversation(session_id: str):
    """
    清除会话

    Args:
        session_id: 会话ID

    Returns:
        操作结果
    """
    try:
        conversation_store.clear_session(session_id)
        return {"message": f"会话 {session_id} 已清除", "success": True}

    except Exception as e:
        logger.error(f"清除会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"清除会话失败: {str(e)}")


@router.get("/conversations")
async def list_conversations():
    """
    列出所有会话

    Returns:
        会话ID列表
    """
    try:
        sessions = conversation_store.get_all_sessions()
        return {
            "sessions": sessions,
            "total": len(sessions)
        }

    except Exception as e:
        logger.error(f"列出会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"列出会话失败: {str(e)}")


@router.get("/health", response_model=HealthStatus)
async def health_check():
    """
    健康检查

    Returns:
        系统健康状态
    """
    try:
        tool_manager = await get_tool_manager()

        tool_groups: Dict[str, List[str]] = {}
        for tool_name in tool_manager.tools.keys():
            server_name, _, short_name = tool_name.partition("_")
            tool_groups.setdefault(server_name, []).append(short_name or tool_name)

        services: Dict[str, Dict[str, Any]] = {}
        for server_name, tool_names in tool_groups.items():
            services[server_name] = {
                "status": "healthy" if tool_manager._initialized else "degraded",
                "connected": tool_manager._initialized,
                "tools_count": len(tool_names),
                "tools": sorted(tool_names),
            }

        if not services:
            services["mcp"] = {
                "status": "down",
                "connected": False,
                "tools_count": 0,
                "tools": [],
            }

        all_healthy = all(s["status"] == "healthy" for s in services.values())
        any_healthy = any(s["status"] == "healthy" for s in services.values())

        if all_healthy:
            overall_status = "healthy"
        elif any_healthy:
            overall_status = "degraded"
        else:
            overall_status = "down"

        return HealthStatus(
            status=overall_status,
            timestamp=datetime.now().isoformat(),
            services=services,
        )

    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return HealthStatus(
            status="down",
            timestamp=datetime.now().isoformat(),
            services={"error": {"status": "unhealthy", "error": str(e)}},
        )