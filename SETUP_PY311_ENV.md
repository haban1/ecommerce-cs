# 创建 Python 3.11 环境并安装依赖的步骤

## 1. 创建 conda 环境
```bash
conda create -n ecommerce-cs-py311 python=3.11 -y
```

## 2. 激活环境
```bash
conda activate ecommerce-cs-py311
```

## 3. 安装项目依赖
```bash
pip install -r requirements.txt
```

## 4. 验证安装
```bash
python --version  # 应该显示 Python 3.11.x
python -c "import mcp; print('MCP installed')"
```

## 5. 测试 MCP server
```bash
python test_minimal_client.py
```

如果测试通过，说明 Python 3.11 环境可以正常运行 MCP。

## 6. 更新启动脚本
修改 `start-mcp-servers.bat` 使用新环境：
```batch
conda activate ecommerce-cs-py311
python -m src.mcp_servers.order_server
```