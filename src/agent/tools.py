"""MCP Client 工具加载模块

目标：把 MCP server 作为“按需子进程”使用。
- 工具发现阶段：启动子进程 -> initialize -> list_tools -> 关闭子进程
- 工具执行阶段：每次调用工具都启动子进程 -> initialize -> call_tool -> 关闭子进程

这样不需要提前手动启动 MCP 服务，也不会持久化后台进程。

Windows 说明：
- MCP stdio 子进程通信依赖 asyncio subprocess 管道。
- 在 Windows 上，默认 ProactorEventLoop 可能导致 subprocess/stdin/stdout 管道行为异常。
- 这里强制使用 WindowsSelectorEventLoopPolicy 以提升兼容性。

关键超时：
- connect_timeout：拉起子进程并建立 stdio 通道的超时
- init_timeout：MCP session.initialize() 的超时
- call_timeout：MCP call_tool() 的超时
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Windows 下强制使用 Selector event loop，避免 stdio MCP 握手卡死
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from ..config import config

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP Server 配置"""

    name: str
    command: str
    args: List[str]
    env: Optional[Dict[str, str]] = None


class MCPToolManager:
    """MCP 工具管理器（按需启动子进程，无持久 session）"""

    def __init__(self):
        self.tools: Dict[str, StructuredTool] = {}
        self._initialized = False

    def get_tool(self, tool_name: str) -> Optional[StructuredTool]:
        return self.tools.get(tool_name)

    def get_all_tools(self) -> List[StructuredTool]:
        return list(self.tools.values())

    def _build_stdio_env(self, overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构造 MCP stdio 子进程环境，默认继承当前进程环境。"""
        env = os.environ.copy()
        if overrides:
            env.update(overrides)
        return env

    def _build_mcp_server_env(self) -> Dict[str, str]:
        """显式传递关键依赖配置，避免子进程回落到 .env 中的 localhost 默认值。"""
        return self._build_stdio_env(
            {
                "MYSQL_HOST": str(config.MYSQL_HOST),
                "MYSQL_PORT": str(config.MYSQL_PORT),
                "MYSQL_USER": str(config.MYSQL_USER),
                "MYSQL_PASSWORD": str(config.MYSQL_PASSWORD),
                "MYSQL_DATABASE": str(config.MYSQL_DATABASE),
                "REDIS_HOST": str(config.REDIS_HOST),
                "REDIS_PORT": str(config.REDIS_PORT),
                "REDIS_PASSWORD": str(config.REDIS_PASSWORD or ""),
                "REDIS_DB": str(config.REDIS_DB),
                "CHROMA_PERSIST_DIR": str(config.CHROMA_PERSIST_DIR),
                "LOG_LEVEL": str(config.LOG_LEVEL),
            }
        )

    async def initialize_default_servers(self) -> None:
        if self._initialized:
            return

        self.tools.clear()

        logger.info("初始化默认 MCP Servers（按需子进程模式），当前解释器: %s", sys.executable)

        server_env = self._build_mcp_server_env()
        server_configs = [
            MCPServerConfig(
                name="order_server",
                command=sys.executable,
                args=["-u", "-m", "src.mcp_servers.order_server"],
                env=server_env,
            ),
            MCPServerConfig(
                name="rag_server",
                command=sys.executable,
                args=["-u", "-m", "src.mcp_servers.rag_server"],
                env=server_env,
            ),
        ]

        all_tools: list[StructuredTool] = []
        for sc in server_configs:
            try:
                discovered = await self._discover_tools(sc)
                all_tools.extend(discovered)
            except Exception as e:
                logger.error("发现 MCP 工具失败 %s: %s", sc.name, e, exc_info=True)

        if not all_tools:
            logger.error("未发现到任何 MCP 工具，MCP Tool Manager 保持未初始化状态")
            self._initialized = False
            return

        self._initialized = True
        logger.info("默认 MCP Servers 工具发现完成，共 %d 个工具", len(all_tools))

    async def _discover_tools(self, server_config: MCPServerConfig) -> List[StructuredTool]:
        """启动子进程，列出工具，再关闭。"""

        connect_timeout = float(getattr(config, "MCP_STDIO_CONNECT_TIMEOUT_S", 15))
        init_timeout = float(getattr(config, "MCP_SESSION_INIT_TIMEOUT_S", 30))

        start_time = time.time()
        logger.info(
            "[%s] 发现工具: command=%s args=%s (connect_timeout=%ss init_timeout=%ss)",
            server_config.name,
            server_config.command,
            server_config.args,
            connect_timeout,
            init_timeout,
        )

        server_params = StdioServerParameters(
            command=server_config.command,
            args=server_config.args,
            env=server_config.env,
        )

        # stdio_client 的 enter（spawn + 管道建立）在 Windows 上也可能卡死。
        # 这里用 asyncio.timeout（不创建新 task）实现超时，避免 anyio 报
        # "Attempted to exit cancel scope in a different task than it was entered in"。
        try:
            async with asyncio.timeout(connect_timeout):
                async with stdio_client(server_params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await asyncio.wait_for(session.initialize(), timeout=init_timeout)
                        tools_response = await session.list_tools()
        except TimeoutError:
            raise asyncio.TimeoutError(f"stdio connect timeout: {server_config.name}")

        tools = tools_response.tools or []
        tool_names = [t.name for t in tools]

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info("[%s] 发现到 %d 个工具: %s (耗时 %.0fms)", server_config.name, len(tools), tool_names, elapsed_ms)

        langchain_tools: list[StructuredTool] = []
        for mcp_tool in tools:
            try:
                tool = self._convert_mcp_tool_to_langchain(mcp_tool, server_config)
                langchain_tools.append(tool)
                self.tools[tool.name] = tool
            except Exception as e:
                logger.error("[%s] 转换工具失败 %s: %s", server_config.name, getattr(mcp_tool, "name", "?"), e, exc_info=True)

        return langchain_tools

    def _convert_mcp_tool_to_langchain(self, mcp_tool: Any, server_config: MCPServerConfig) -> StructuredTool:
        """将 MCP Tool 转换为 LangChain StructuredTool。"""

        input_schema = mcp_tool.inputSchema
        args_schema = self._create_pydantic_model_from_schema(input_schema, f"{mcp_tool.name}_input")

        async def tool_func(**kwargs) -> str:
            """每次调用都启动子进程 -> call_tool -> 关闭。"""

            connect_timeout = float(getattr(config, "MCP_STDIO_CONNECT_TIMEOUT_S", 15))
            init_timeout = float(getattr(config, "MCP_SESSION_INIT_TIMEOUT_S", 30))
            call_timeout = float(getattr(config, "MCP_TOOL_CALL_TIMEOUT_S", 30))

            server_params = StdioServerParameters(
                command=server_config.command,
                args=server_config.args,
                env=self._build_stdio_env(server_config.env),
            )

            start_time = time.time()
            logger.info(
                "调用 MCP 工具: %s.%s args=%s (connect=%ss init=%ss call=%ss)",
                server_config.name,
                mcp_tool.name,
                kwargs,
                connect_timeout,
                init_timeout,
                call_timeout,
            )

            # stdio_client 的 enter（spawn + 管道建立）在 Windows 上也可能卡死，这里加 connect_timeout
            client_cm = stdio_client(server_params)
            try:
                read_stream, write_stream = await asyncio.wait_for(client_cm.__aenter__(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                try:
                    await client_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                return f"错误: 连接 MCP Server 超时（{server_config.name}）"
            except Exception as e:
                try:
                    await client_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                logger.error("stdio_client enter 失败 %s: %s", server_config.name, e, exc_info=True)
                return f"错误: 连接 MCP Server 失败（{server_config.name}）: {str(e)}"

            try:
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=init_timeout)
                    result = await asyncio.wait_for(session.call_tool(mcp_tool.name, kwargs), timeout=call_timeout)

                if result.content:
                    texts: list[str] = []
                    for content in result.content:
                        if hasattr(content, "text"):
                            texts.append(content.text)
                    out = "\n".join(texts)
                else:
                    out = "工具执行成功，但无返回内容"

                elapsed_ms = (time.time() - start_time) * 1000
                logger.info("MCP 工具完成: %s.%s (耗时 %.0fms)", server_config.name, mcp_tool.name, elapsed_ms)
                return out

            except asyncio.TimeoutError:
                return f"错误: 调用工具超时（{server_config.name}.{mcp_tool.name}）"
            except Exception as e:
                logger.error("工具调用失败 %s.%s: %s", server_config.name, mcp_tool.name, e, exc_info=True)
                return f"错误: {str(e)}"
            finally:
                try:
                    await client_cm.__aexit__(None, None, None)
                except Exception:
                    pass

        return StructuredTool(
            name=f"{server_config.name}_{mcp_tool.name}",
            description=mcp_tool.description or f"Tool from {server_config.name}",
            func=tool_func,
            coroutine=tool_func,
            args_schema=args_schema,
        )

    def _create_pydantic_model_from_schema(self, json_schema: Dict[str, Any], model_name: str) -> type[BaseModel]:
        properties = json_schema.get("properties", {})
        required = json_schema.get("required", [])

        fields: dict[str, tuple[type, Any]] = {}
        for field_name, field_schema in properties.items():
            field_type = self._json_type_to_python_type(field_schema)
            field_description = field_schema.get("description", "")
            field_default = ... if field_name in required else None

            fields[field_name] = (field_type, Field(default=field_default, description=field_description))

        return create_model(model_name, **fields)

    def _json_type_to_python_type(self, field_schema: Dict[str, Any]) -> type:
        json_type = field_schema.get("type", "string")

        type_mapping: dict[str, type] = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        python_type = type_mapping.get(json_type, str)

        # 处理可选字段
        if field_schema.get("default") is not None or json_type == "null":
            python_type = Optional[python_type]  # type: ignore[assignment]

        return python_type


_tool_manager: Optional[MCPToolManager] = None
_tool_manager_lock: Optional[asyncio.Lock] = None


async def get_tool_manager() -> MCPToolManager:
    global _tool_manager, _tool_manager_lock

    if _tool_manager_lock is None:
        _tool_manager_lock = asyncio.Lock()

    async with _tool_manager_lock:
        if _tool_manager is None:
            _tool_manager = MCPToolManager()

        if not _tool_manager._initialized:
            await _tool_manager.initialize_default_servers()

        return _tool_manager


async def get_all_mcp_tools() -> List[StructuredTool]:
    import os

    if os.getenv("SKIP_MCP_INIT", "").lower() in ("true", "1", "yes"):
        logger.warning("跳过 MCP 工具初始化（SKIP_MCP_INIT=true）")
        return []

    manager = await get_tool_manager()
    return manager.get_all_tools()