"""
Gradio ChatInterface
电商智能客服系统的 Web UI（方案 A：Gradio 仅调用后端 API）

说明：
- Gradio 不再直接运行 LangGraph / MCP Tool Manager
- 所有对话与工具调用都由 FastAPI 后端完成
- 本文件只负责：UI + 调用 /api/chat 与 /api/health
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import gradio as gr
import httpx

from ..config import config

logger = logging.getLogger(__name__)


# ==================== 后端 API 配置 ====================

# 0.0.0.0 不能作为客户端目标地址，默认用 127.0.0.1
DEFAULT_API_BASE_URL = f"http://127.0.0.1:{config.API_PORT}"
API_BASE_URL = os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


# ==================== 会话/用户辅助 ====================

def infer_user_id_from_message(message: str, default: str = "gradio_user") -> str:
    """从用户消息中提取用户ID，未命中则使用默认值。"""
    match = re.search(r"\b(USER\d{3,})\b", message, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return default


SESSION_ID = f"session_{int(time.time())}"
CURRENT_KNOWLEDGE_TYPE = "product_manual"


def set_current_knowledge_type(knowledge_type: str):
    """同步当前知识类型，供上传时复用。"""
    global CURRENT_KNOWLEDGE_TYPE
    CURRENT_KNOWLEDGE_TYPE = knowledge_type or "product_manual"
    return CURRENT_KNOWLEDGE_TYPE


# ==================== 后端 API 调用 ====================

async def api_chat(message: str, user_id: str, session_id: str) -> str:
    """调用后端 /api/chat 获取回复文本。"""
    url = f"{API_BASE_URL}/api/chat"
    payload = {
        "message": message,
        "user_id": user_id,
        "session_id": session_id,
        "stream": False,
    }

    timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response") or "抱歉，我暂时无法回答这个问题。"


async def api_health() -> Dict[str, Any]:
    """调用后端 /api/health 获取系统状态。"""
    url = f"{API_BASE_URL}/api/health"
    timeout = httpx.Timeout(connect=3.0, read=10.0, write=3.0, pool=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


# ==================== Gradio 业务逻辑 ====================

async def chat_with_backend(message: str) -> Tuple[str, str, List[str]]:
    """与后端对话，返回 (回复, 指标, 日志行)。"""
    start_time = time.time()
    user_id = infer_user_id_from_message(message)

    try:
        response_text = await api_chat(message=message, user_id=user_id, session_id=SESSION_ID)
        total_ms = (time.time() - start_time) * 1000
        metrics = f"⏱️ 总计: {total_ms:.0f}ms | API: {API_BASE_URL}"
        return response_text, metrics, []

    except httpx.HTTPStatusError as e:
        total_ms = (time.time() - start_time) * 1000
        # 尝试提取后端 detail
        detail = ""
        try:
            detail_json = e.response.json()
            detail = detail_json.get("detail") or json.dumps(detail_json, ensure_ascii=False)
        except Exception:
            detail = e.response.text

        msg = (
            f"❌ 后端返回错误（HTTP {e.response.status_code}）\n"
            f"API: {API_BASE_URL}\n"
            f"detail: {detail}"
        )
        metrics = f"⏱️ 总计: {total_ms:.0f}ms | API: {API_BASE_URL}"
        return msg, metrics, []

    except Exception as e:
        total_ms = (time.time() - start_time) * 1000
        msg = (
            "❌ 无法连接后端 API。\n"
            f"API: {API_BASE_URL}\n"
            f"错误: {type(e).__name__}: {str(e)}\n"
            "请确认 FastAPI 已启动（http://127.0.0.1:8080/health）。"
        )
        metrics = f"⏱️ 总计: {total_ms:.0f}ms | API: {API_BASE_URL}"
        return msg, metrics, []


def chat_wrapper(message: str) -> Tuple[str, str, str]:
    """同步包装器（给 Gradio 用）。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        response, metrics, logs = loop.run_until_complete(chat_with_backend(message))
        return response, metrics, "\n".join(logs)
    finally:
        loop.close()


# ==================== 知识库管理 ====================

async def _read_upload_bytes(file_obj) -> tuple[str, bytes]:
    """兼容 Gradio 不同版本的文件对象。"""
    if file_obj is None:
        raise ValueError("请选择文件")

    file_path = getattr(file_obj, "name", None) or getattr(file_obj, "path", None)
    if not file_path:
        raise ValueError("无法读取上传文件路径")

    path = Path(file_path)
    return path.name, path.read_bytes()


async def upload_document(file, knowledge_type: str = "product_manual"):
    """上传文档到后端知识库并立即索引。"""
    try:
        if file is None:
            return "❌ 请选择文件"

        file_name, file_bytes = await _read_upload_bytes(file)
        url = f"{API_BASE_URL}/api/knowledge/upload"
        timeout = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                data={"knowledge_type": knowledge_type},
                files={"file": (file_name, file_bytes)},
            )
            resp.raise_for_status()
            data = resp.json()

        return (
            f"✅ {data.get('message', '上传成功')}\n"
            f"文件: {data.get('filename', file_name)}\n"
            f"知识类型: {data.get('knowledge_type', knowledge_type)}\n"
            f"collection: {data.get('collection_name', '')}\n"
            f"路径: {data.get('saved_path', '')}"
        )

    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", e.response.text)
        except Exception:
            detail = e.response.text
        logger.error(f"上传文档失败: HTTP {e.response.status_code} {detail}")
        return f"❌ 上传失败（HTTP {e.response.status_code}）: {detail}"
    except Exception as e:
        logger.error(f"上传文档失败: {e}")
        return f"❌ 上传失败: {str(e)}"


async def rebuild_index(knowledge_type: str = "product_manual"):
    """重建知识库索引。"""
    try:
        url = f"{API_BASE_URL}/api/knowledge/reindex"
        timeout = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)
        data = {"knowledge_type": knowledge_type, "clear_existing": "true"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, data=data)
            resp.raise_for_status()
            payload = resp.json()

        results = payload.get("results", {})
        summary = ", ".join(f"{name}:{count}" for name, count in results.items()) or "无文件"
        return (
            f"✅ {payload.get('message', '重建完成')}\n"
            f"知识类型: {payload.get('knowledge_type', knowledge_type)}\n"
            f"source: {payload.get('source_dir', '')}\n"
            f"结果: {summary}"
        )
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", e.response.text)
        except Exception:
            detail = e.response.text
        logger.error(f"重建索引失败: HTTP {e.response.status_code} {detail}")
        return f"❌ 重建失败（HTTP {e.response.status_code}）: {detail}"
    except Exception as e:
        logger.error(f"重建索引失败: {e}")
        return f"❌ 重建失败: {str(e)}"


def upload_document_sync(file, knowledge_type: str = "product_manual"):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(upload_document(file, knowledge_type))
    finally:
        loop.close()


def rebuild_index_sync(knowledge_type: str = "product_manual"):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(rebuild_index(knowledge_type))
    finally:
        loop.close()


# ==================== MCP/系统状态面板（由后端提供）====================

async def get_mcp_status_html() -> str:
    """从后端 /api/health 渲染 MCP/服务状态。"""
    try:
        health = await api_health()

        status = health.get("status", "unknown")
        ts = health.get("timestamp", "")
        services: Dict[str, Any] = health.get("services") or {}

        html = "<div style='padding: 10px;'>"
        html += "<h3>🔌 系统状态</h3>"
        html += f"<p><b>API</b>: {API_BASE_URL}</p>"
        html += f"<p><b>overall</b>: {status} <span style='color: gray;'>{ts}</span></p>"

        if not services:
            html += "<p style='color: gray;'>暂无服务状态</p>"
        else:
            html += "<ul>"
            for name, info in services.items():
                if isinstance(info, dict):
                    s = info.get("status", "unknown")
                    connected = info.get("connected")
                    tools_count = info.get("tools_count")
                    icon = "🟢" if s == "healthy" else ("🟡" if s == "degraded" else "🔴")
                    extra = []
                    if connected is not None:
                        extra.append(f"connected={connected}")
                    if tools_count is not None:
                        extra.append(f"tools={tools_count}")
                    extra_text = (" (" + ", ".join(extra) + ")") if extra else ""
                    html += f"<li>{icon} <b>{name}</b>: {s}{extra_text}</li>"
                else:
                    html += f"<li>🔴 <b>{name}</b>: {str(info)}</li>"
            html += "</ul>"

        html += "</div>"
        return html

    except Exception as e:
        return (
            "<div style='padding: 10px;'>"
            "<h3>🔌 系统状态</h3>"
            f"<p><b>API</b>: {API_BASE_URL}</p>"
            f"<p style='color: #b00020;'>无法获取 /api/health：{type(e).__name__}: {str(e)}</p>"
            "</div>"
        )


def get_mcp_status_html_sync() -> str:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(get_mcp_status_html())
    finally:
        loop.close()

APP_THEME = gr.themes.Soft()
APP_CSS = """
.metrics-box {
    background-color: #f0f0f0;
    padding: 10px;
    border-radius: 5px;
    margin-top: 5px;
    font-size: 12px;
}
.log-panel {
    font-family: monospace;
    font-size: 11px;
    background-color: #1e1e1e;
    color: #d4d4d4;
    padding: 10px;
    border-radius: 5px;
    max-height: 300px;
    overflow-y: auto;
}
"""


def create_gradio_interface():
    with gr.Blocks(title="电商智能客服系统") as demo:
        gr.Markdown("# 🤖 电商智能客服系统")
        gr.Markdown("Gradio 仅作为 UI，通过 FastAPI 调用 Agent。")

        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(label="对话区", height=500)

                metrics_display = gr.Textbox(
                    label="性能指标",
                    interactive=False,
                    lines=2,
                    elem_classes=["metrics-box"],
                )

                with gr.Row():
                    msg_input = gr.Textbox(label="输入消息", placeholder="请输入您的问题...", scale=4)
                    send_btn = gr.Button("发送", variant="primary", scale=1)

                gr.Examples(
                    examples=[
                        "USER001 我买的什么？",
                        "USER001 查询订单",
                        "帮我查询订单 ORD20240101001 的物流信息",
                        "如何申请退款？",
                    ],
                    inputs=msg_input,
                )

            with gr.Column(scale=1):
                with gr.Accordion("🔌 系统状态", open=True):
                    mcp_status_display = gr.HTML(value=get_mcp_status_html_sync())
                    refresh_status_btn = gr.Button("🔄 刷新状态", size="sm")

                with gr.Accordion("📚 知识库管理", open=False):
                    knowledge_type = gr.Dropdown(
                        choices=[
                            ("产品手册", "product_manual"),
                            ("政策文档", "policy"),
                            ("FAQ", "faq"),
                        ],
                        value="product_manual",
                        label="知识类型",
                    )
                    file_upload = gr.File(label="上传文档", file_types=[".pdf", ".txt", ".md"])
                    upload_btn = gr.Button("📤 上传文档", variant="primary")
                    upload_status = gr.Textbox(label="上传状态", interactive=False, lines=3)
                    rebuild_btn = gr.Button("🔨 重建索引", variant="secondary")

                with gr.Accordion("📋 工具调用日志", open=True):
                    log_display = gr.Textbox(
                        label="最近 20 条记录",
                        interactive=False,
                        lines=10,
                        elem_classes=["log-panel"],
                    )

        def respond(message: str, chat_history: List[Dict[str, str]]):
            if not message.strip():
                return "", chat_history, "", ""

            chat_history = list(chat_history or [])
            chat_history.append({"role": "user", "content": message})

            response, metrics, logs = chat_wrapper(message)
            chat_history.append({"role": "assistant", "content": response})

            return "", chat_history, metrics, logs

        send_btn.click(
            fn=respond,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot, metrics_display, log_display],
        )

        msg_input.submit(
            fn=respond,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot, metrics_display, log_display],
        )

        refresh_status_btn.click(fn=get_mcp_status_html_sync, outputs=mcp_status_display)

        upload_btn.click(fn=upload_document_sync, inputs=[file_upload, knowledge_type], outputs=upload_status)
        rebuild_btn.click(fn=rebuild_index_sync, inputs=[knowledge_type], outputs=upload_status)

    return demo


def launch_gradio_app(server_name: str = "0.0.0.0", server_port: int = 7860, share: bool = False):
    logger.info(f"启动 Gradio UI: {server_name}:{server_port} (API={API_BASE_URL})")
    demo = create_gradio_interface()
    demo.launch(
        server_name=server_name,
        server_port=server_port,
        share=share,
        show_error=True,
        theme=APP_THEME,
        css=APP_CSS,
    )


if __name__ == "__main__":
    launch_gradio_app()