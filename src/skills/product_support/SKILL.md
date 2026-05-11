---
name: product_support
version: 1.0.0
description: 提供产品使用指导和故障排除
tools:
  - rag_server_search_knowledge
priority: medium
---

# 产品支持 Skill

## 功能描述
提供产品使用指导和故障排除，包括：
- 产品功能说明
- 使用方法指导
- 常见故障排除
- 设置和配对指南

## 工作流程

### 1. 识别产品和问题
从用户输入中提取：
- 产品名称（如：蓝牙耳机、智能手表）
- 具体问题（如：配对、连接、设置）

### 2. 检索产品手册
调用知识检索工具：
```
search_knowledge(
  query="用户问题",
  knowledge_type="product_manual",
  filter_field="product_name",
  filter_value="产品名称",
  top_k=5
)
```

### 3. 扩展上下文（如需要）
如果检索结果不够详细，使用：
```
get_full_document_context(
  chunk_id="检索到的chunk_id",
  window_size=3
)
```

### 4. 格式化回复
用通俗易懂的语言解释：
- 分步骤说明操作方法
- 提供关键注意事项
- 如果无法解决，建议查看完整说明书或联系售后

## 示例对话

**用户**: 蓝牙耳机怎么配对  
**助手**: [调用 search_knowledge] 蓝牙耳机配对步骤：
1. 长按电源键3秒，指示灯闪烁进入配对模式
2. 打开手机蓝牙，搜索设备
3. 选择"AirPods Pro"连接
4. 听到提示音表示配对成功

**用户**: 耳机连不上手机  
**助手**: [调用 search_knowledge] 请尝试以下排查步骤：
1. 确认耳机电量充足
2. 重启手机蓝牙
3. 删除已配对设备，重新配对
4. 检查是否在其他设备上连接中

如果仍无法解决，建议联系售后：400-XXX-XXXX

## 注意事项
- 优先提供简洁的操作步骤
- 避免使用过于专业的术语
- 如果检索结果不够完整，主动扩展上下文
- 无法解决的问题，引导用户联系专业售后