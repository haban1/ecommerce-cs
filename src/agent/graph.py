"""
LangGraph StateGraph 定义
电商客服 Agent 的状态图和节点编排
"""

import logging
import json
from typing import TypedDict, List, Dict, Any, Literal, Annotated
import operator
import uuid

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from ..config import config
from .tools import get_all_mcp_tools
from .prompts import get_system_prompt
from ..skills.loader import get_skill_loader

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Agent 状态定义"""

    messages: Annotated[List, operator.add]  # 对话历史（支持追加）
    user_id: str  # 当前用户ID
    current_order_id: str  # 当前上下文中的订单号
    retrieval_context: str  # 检索召回的知识
    tool_results: List[Dict[str, Any]]  # 工具调用结果
    final_response: str  # 最终回复
    tool_call_count: int  # 工具调用次数
    intent: str  # 用户意图
    active_skill: str  # 当前激活的 Skill 名称


class SkillRouteDecision(BaseModel):
    """LLM 输出的 Skill 路由结果。"""

    skill_name: str = Field(default="", description="命中的 Skill 名称；未命中时返回空字符串")
    reason: str = Field(default="", description="选择该 Skill 的简要原因")


async def route_skill(state: AgentState) -> AgentState:
    """Skill 路由节点：让 LLM 基于所有 Skill 元数据选择最合适的 Skill。"""

    logger.info("执行 Skill 路由（LLM）...")

    last_message = state["messages"][-1]
    user_input = last_message.content if isinstance(last_message, HumanMessage) else ""

    skill_loader = get_skill_loader()
    catalog = json.dumps(skill_loader.get_skill_routing_metadata(), ensure_ascii=False, indent=2)

    router_llm = ChatOpenAI(
        model=config.LLM_MODEL_NAME,
        base_url=config.VLLM_API_BASE,
        api_key=config.VLLM_API_KEY or "EMPTY",
        temperature=0,
    )

    router = router_llm.with_structured_output(SkillRouteDecision)
    router_messages = [
        SystemMessage(
            content=(
                "你是一个 Skill 路由器。你必须只基于下面给出的 Skill 元数据做选择，不要自己发明新 Skill。"
                "如果用户问题明显属于某个 Skill，就返回该 Skill 的 name；如果都不合适，返回空字符串。\n\n"
                f"可选 Skill 列表：\n{catalog}"
            )
        ),
        HumanMessage(content=user_input),
    ]

    matched_skill = None
    try:
        decision = await router.ainvoke(router_messages)
        chosen_name = (decision.skill_name or "").strip()
        if chosen_name:
            matched_skill = skill_loader.get_skill(chosen_name)
            if matched_skill is None:
                logger.warning("LLM 返回了未知 Skill: %s", chosen_name)
        if matched_skill:
            logger.info("LLM 匹配到 Skill: %s (%s)", matched_skill.name, decision.reason)
    except Exception as e:
        logger.error("LLM Skill 路由失败，降级到规则匹配: %s", e, exc_info=True)
        matched_skill = skill_loader.match_skill(user_input)

    if matched_skill:
        state["active_skill"] = matched_skill.name
        state["intent"] = matched_skill.name
        logger.info(f"匹配到 Skill: {matched_skill.name}")
    else:
        state["active_skill"] = ""
        state["intent"] = "general"
        logger.info("未匹配到特定 Skill，使用通用对话")

    return state


async def generate_response(state: AgentState) -> AgentState:
    """生成回复节点：基于检索上下文和工具结果生成最终回复。"""

    logger.info("生成最终回复...")

    llm = ChatOpenAI(
        model=config.LLM_MODEL_NAME,
        base_url=config.VLLM_API_BASE,
        api_key=config.VLLM_API_KEY or "EMPTY",
        temperature=0.7,
    )

    messages = [SystemMessage(content=get_system_prompt())]
    messages.extend(state["messages"])

    if state.get("retrieval_context"):
        messages.append(
            SystemMessage(content=f"检索到的相关知识：\n{state['retrieval_context']}")
        )

    try:
        response = await llm.ainvoke(messages)
        final_response = response.content

        state["final_response"] = final_response
        state["messages"].append(AIMessage(content=final_response))

        logger.info("回复生成完成")

    except Exception as e:
        logger.error(f"生成回复失败: {e}")
        error_message = "抱歉，我遇到了一些问题。请稍后再试或联系人工客服。"
        state["final_response"] = error_message
        state["messages"].append(AIMessage(content=error_message))

    return state


def should_continue(state: AgentState) -> Literal["call_tools", "generate_response"]:
    """条件边：判断是否需要继续调用工具。"""

    tool_call_count = state.get("tool_call_count", 0)
    if tool_call_count >= 3:
        logger.info("工具调用次数达到上限，生成最终回复")
        return "generate_response"

    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        logger.info("检测到工具调用，继续执行")
        return "call_tools"

    logger.info("无需继续调用工具，生成最终回复")
    return "generate_response"


async def compile_app():
    """编译 StateGraph 并返回可执行的 App。"""

    logger.info("正在编译 Agent StateGraph...")

    tools = await get_all_mcp_tools()
    logger.info(f"加载了 {len(tools)} 个工具")

    llm = ChatOpenAI(
        model=config.LLM_MODEL_NAME,
        base_url=config.VLLM_API_BASE,
        api_key=config.VLLM_API_KEY or "EMPTY",
        temperature=0.7,
    )

    tool_node = ToolNode(tools)

    def _forced_tool_call_message(tool_name: str, args: dict[str, Any]) -> AIMessage:
        call_id = f"forced_{uuid.uuid4().hex[:8]}"
        return AIMessage(
            content="",
            tool_calls=[{"id": call_id, "type": "tool_call", "name": tool_name, "args": args}],
        )

    def _pick_forced_tool_call(
        active_skill_name: str,
        skill_tool_names: set[str],
        user_text: str,
        user_id: str | None,
    ) -> AIMessage | None:
        """当模型没有主动发起工具调用时，选择一个最小可行的工具请求。"""

        if active_skill_name == "order_query":
            if "order_server_extract_order_id_from_text" in skill_tool_names:
                return _forced_tool_call_message(
                    "order_server_extract_order_id_from_text",
                    {"text": user_text},
                )

            if user_id and "order_server_search_orders" in skill_tool_names:
                return _forced_tool_call_message(
                    "order_server_search_orders",
                    {"user_id": user_id, "limit": 5},
                )

        if active_skill_name == "after_sales":
            if "order_server_extract_order_id_from_text" in skill_tool_names:
                return _forced_tool_call_message(
                    "order_server_extract_order_id_from_text",
                    {"text": user_text},
                )

            if "rag_server_search_knowledge" in skill_tool_names:
                return _forced_tool_call_message(
                    "rag_server_search_knowledge",
                    {"query": user_text, "knowledge_type": "policy", "top_k": 5},
                )

        if active_skill_name == "product_support" and "rag_server_search_knowledge" in skill_tool_names:
            return _forced_tool_call_message(
                "rag_server_search_knowledge",
                {"query": user_text, "knowledge_type": "product_manual", "top_k": 5},
            )

        if active_skill_name == "general_service" and "rag_server_search_knowledge" in skill_tool_names:
            return _forced_tool_call_message(
                "rag_server_search_knowledge",
                {"query": user_text, "knowledge_type": "faq", "top_k": 5},
            )

        if "rag_server_search_knowledge" in skill_tool_names:
            return _forced_tool_call_message(
                "rag_server_search_knowledge",
                {"query": user_text, "knowledge_type": "faq", "top_k": 5},
            )

        return None

    def _get_last_executed_tool_signature(messages: list[Any]) -> tuple[str, str] | None:
        """从消息历史中提取“最近一次已执行”的工具调用签名（tool_name + args_json）。

        用于避免模型在拿到 ToolMessage 后重复再调用同一个工具。
        """

        # 先找到最近一个 ToolMessage
        last_tool_msg: ToolMessage | None = None
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                last_tool_msg = m
                break
        if last_tool_msg is None:
            return None

        tool_call_id = getattr(last_tool_msg, "tool_call_id", None)
        if not tool_call_id:
            return None

        # 找到对应的 AIMessage tool_call
        for m in reversed(messages):
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    if tc.get("id") == tool_call_id:
                        import json

                        return (tc.get("name", ""), json.dumps(tc.get("args", {}), sort_keys=True, ensure_ascii=False))

        return None

    async def execute_skill(state: AgentState) -> AgentState:
        """Skill 执行节点：注入 Skill Prompt + 绑定该 Skill 的工具，LLM 输出 tool_calls 或最终文本。"""

        messages = [SystemMessage(content=get_system_prompt())]

        active_skill_name = state.get("active_skill")
        skill_loader = get_skill_loader()
        skill_tools = tools

        if active_skill_name:
            skill = skill_loader.get_skill(active_skill_name)
            if skill:
                messages.append(SystemMessage(content=skill_loader.get_skill_prompt(skill)))

                skill_tool_names = set(skill.tools)
                skill_tools = [t for t in tools if t.name in skill_tool_names]
                logger.info(f"使用 Skill: {skill.name}, 工具数: {len(skill_tools)}")

        messages.extend(state["messages"])

        if state.get("user_id"):
            messages.append(SystemMessage(content=f"当前用户ID: {state['user_id']}"))

        if state["messages"] and isinstance(state["messages"][-1], ToolMessage):
            logger.info("检测到上一轮工具结果，继续生成回复")

        llm_with_skill_tools = llm.bind_tools(skill_tools)
        response = await llm_with_skill_tools.ainvoke(messages)

        # 防重复：当上一条是 ToolMessage 时，模型有时会“再次调用同一个工具”。
        # 这会导致无意义的重复调用（甚至触发超时）。这里直接拦截掉。
        if state["messages"] and isinstance(state["messages"][-1], ToolMessage) and response.tool_calls:
            last_sig = _get_last_executed_tool_signature(state["messages"])
            if last_sig is not None:
                import json

                first = response.tool_calls[0]
                first_sig = (
                    first.get("name", ""),
                    json.dumps(first.get("args", {}), sort_keys=True, ensure_ascii=False),
                )
                if first_sig == last_sig:
                    logger.info("检测到重复工具调用，已拦截: %s", first_sig[0])
                    response = AIMessage(content=response.content or "")

        if (
            active_skill_name
            and skill_tools
            and not response.tool_calls
            and isinstance(state["messages"][-1], HumanMessage)
        ):
            forced = _pick_forced_tool_call(
                active_skill_name=active_skill_name,
                skill_tool_names={t.name for t in skill_tools},
                user_text=state["messages"][-1].content or "",
                user_id=state.get("user_id"),
            )
            if forced is not None:
                logger.info(
                    "模型未主动发起工具调用，Graph 级强制进入 ToolNode: skill=%s tool=%s",
                    active_skill_name,
                    forced.tool_calls[0]["name"],
                )
                state["tool_call_count"] = state.get("tool_call_count", 0) + 1
                return {"messages": [forced]}

        if response.tool_calls:
            state["tool_call_count"] = state.get("tool_call_count", 0) + 1

        return {"messages": [response]}

    workflow = StateGraph(AgentState)

    workflow.add_node("route_skill", route_skill)
    workflow.add_node("execute_skill", execute_skill)
    workflow.add_node("call_tools", tool_node)
    workflow.add_node("generate", generate_response)

    workflow.set_entry_point("route_skill")

    workflow.add_edge("route_skill", "execute_skill")

    workflow.add_conditional_edges(
        "execute_skill",
        should_continue,
        {"call_tools": "call_tools", "generate_response": "generate"},
    )

    workflow.add_edge("call_tools", "execute_skill")
    workflow.add_edge("generate", END)

    app = workflow.compile()
    logger.info("StateGraph 编译完成")

    return app


_app = None


async def get_agent_app():
    """获取编译后的 Agent App"""

    global _app
    if _app is None:
        _app = await compile_app()
    return _app