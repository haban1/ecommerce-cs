#!/usr/bin/env python3
"""
最简单的 LLM Agent —— 连 MCP server 拿工具 → 调 DeepSeek → 执行工具

用法:
    set DEEPSEEK_API_KEY=sk-xxx   (或者改下面 LLM_API_KEY)
    python agent.py
    > 帮我算 123 + 456
    > 用中文跟 Haban 打招呼
    > quit
"""

import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

# ── 配置 ──────────────────────────────────────────────────
SERVER_SCRIPT = __file__.replace("agent.py", "server.py")
LLM_MODEL = "deepseek-chat"
LLM_BASE_URL = "https://api.deepseek.com"
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-d548d66b1d5843a1bbcd7c2364d94346")  # ← 改成你的 key

# ── MCP 工具 → OpenAI function calling 格式 ─────────────────
def mcp_tools_to_openai(tools):
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    ]


async def main():
    if LLM_API_KEY == "sk-YOUR-KEY":
        print("请设置 DEEPSEEK_API_KEY 环境变量或在脚本里改 LLM_API_KEY")
        sys.exit(1)

    llm = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    messages = [{"role": "system", "content": "你是一个有帮助的助手。收到用户请求后，选择合适的工具完成任务。"}]

    # 1. 启动 MCP server（stdio 子进程）
    server_params = StdioServerParameters(command="python", args=[SERVER_SCRIPT])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 2. 获取工具列表
            tools_result = await session.list_tools()
            tools = tools_result.tools
            openai_tools = mcp_tools_to_openai(tools)

            if not tools:
                print("MCP server 没有提供工具，退出")
                return

            print(f"已连接 MCP server，可用工具: {[t.name for t in tools]}")
            print('输入问题（输入 quit 退出）\n')

            # 3. Agent 循环
            while True:
                user_input = input("> ").strip()
                if not user_input:
                    continue
                if user_input.lower() == "quit":
                    break

                messages.append({"role": "user", "content": user_input})

                # 调 LLM
                response = await llm.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                )

                choice = response.choices[0].message
                messages.append(choice.model_dump(exclude_none=True))

                # LLM 直接回了文字（不需要调工具）
                if choice.content:
                    print(f"🤖 {choice.content}")

                # LLM 要调工具
                if choice.tool_calls:
                    for tc in choice.tool_calls:
                        tool_name = tc.function.name
                        tool_args = json.loads(tc.function.arguments)
                        print(f"🔧 调用工具: {tool_name}({tool_args})")

                        # 4. 通过 MCP 执行工具
                        result = await session.call_tool(tool_name, tool_args)
                        tool_output = result.content[0].text if result.content else str(result)
                        print(f"📋 结果: {tool_output}")

                        # 把结果返回给 LLM
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_output,
                        })

                    # 5. LLM 根据工具结果生成最终回复
                    final = await llm.chat.completions.create(
                        model=LLM_MODEL,
                        messages=messages,
                    )
                    print(f"🤖 {final.choices[0].message.content}")


if __name__ == "__main__":
    asyncio.run(main())
