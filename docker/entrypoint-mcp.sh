#!/bin/bash
set -e

# 启动 MCP Server
# 使用 cat 提供持续的输入流，防止 stdio_server 退出
cat | python -m "$@"