---
name: order_query
version: 1.0.0
description: 查询订单信息和物流状态
triggers:
  - 买
  - 订单
  - 我的订单
  - 查询订单
  - 查订单
  - 订单号
  - 物流单号
  - 下单
  - 发货
  - 配送
  - 追踪
  - 订单查询
keywords:
  - 物流
  - 快递
  - 发货
  - 包裹
  - 运单
  - 收货
tools:
  - order_server_search_orders
  - order_server_get_order_detail
  - order_server_extract_order_id_from_text
priority: high
---

# 订单查询 Skill

## 功能描述
处理用户的订单查询请求，包括：
- 查询历史订单列表
- 查看订单详情
- 追踪物流信息

## 工作流程

### 1. 提取订单号（如果用户提到）
使用 `extract_order_id_from_text` 从用户输入中提取订单号。

### 2. 查询订单
- **如果有订单号**：调用 `get_order_detail` 获取详情
- **如果没有订单号**：调用 `search_orders` 搜索用户的订单列表
  - 必需参数：user_id
  - 可选参数：keyword（商品名）、start_date、end_date、status

### 3. 格式化回复
清晰展示订单信息：
- 订单号
- 商品名称
- 订单金额
- 订单状态
- 物流单号（如有）
- 下单时间

## 示例对话

**用户**: 我想查一下我的订单  
**助手**: 好的，我帮您查询订单。请问您记得订单号吗？或者告诉我商品名称也可以。

**用户**: 订单号是 DD20260505123456  
**助手**: [调用 get_order_detail] 您的订单详情如下：
- 订单号：DD20260505123456
- 商品：蓝牙耳机
- 金额：299元
- 状态：已发货
- 物流单号：SF1234567890

**用户**: 我买的耳机到哪了  
**助手**: [调用 search_orders(keyword="耳机")] 找到您的订单，物流信息显示...

## 注意事项
- 始终先确认用户身份（user_id）
- 如果用户提到订单号但格式不规范，使用 extract_order_id_from_text 提取
- 订单状态包括：paid（已支付）、shipped（已发货）、delivered（已签收）