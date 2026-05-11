#!/usr/bin/env python3
"""最简单的 MCP server —— 提供 add 和 greet 两个工具"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def add(a: int, b: int) -> str:
    """两数相加"""
    return f"{a} + {b} = {a + b}"


@mcp.tool()
def greet(name: str, language: str = "en") -> str:
    """用不同语言打招呼

    Args:
        name: 名字
        language: zh / en / ja
    """
    greetings = {
        "zh": f"你好，{name}！",
        "en": f"Hello, {name}!",
        "ja": f"こんにちは、{name}！",
    }
    return greetings.get(language, greetings["en"])


if __name__ == "__main__":
    mcp.run()
