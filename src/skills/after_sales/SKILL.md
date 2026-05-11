---
name: after_sales
version: 1.0.0
description: 处理售后相关问题，包括退换货、退款查询、政策咨询
triggers:
  - 退款
  - 退货
  - 退换
  - 换货
  - 申请退
  - 退钱
  - 退款进度
  - 政策
  - 规定
  - 条款
  - 保修
  - 质保
keywords:
  - 售后
  - 退换货
  - 退款
  - 保修
  - 质保
  - 政策
  - 条款
  - 规则
tools:
  - order_server_get_refund_status
  - order_server_get_order_detail
  - order_server_extract_order_id_from_text
  - rag_server_search_knowledge
priority: high
---

# 售后处理 Skill

## 功能描述
处理用户的售后相关咨询，包括：
- 查询退款状态和进度
- 解答退换货政策
- 保修条款咨询
- 提供退款时间预估

## 工作流程

### 1. 识别售后类型
- **退款查询**：需要订单号
- **政策咨询**：检索相关政策文档

### 2. 退款查询流程
**获取订单号**：
- 如果用户提供订单号：使用 `extract_order_id_from_text` 提取
- 如果没有订单号：引导用户提供

**查询退款状态**：
```
get_refund_status(order_id)
```

返回信息：
- 退款金额
- 退款状态：pending（申请中）、processing（处理中）、completed（已退款）、rejected（已拒绝）
- 预计到账时间
- 拒绝原因（如有）

### 3. 政策咨询流程
**检索政策文档**：
```
search_knowledge(
  query="用户问题",
  knowledge_type="policy",
  filter_field="policy_type",
  filter_value="return/warranty/privacy/tos"
)
```

**提取关键条款**：
- 适用范围
- 具体规定
- 时间限制
- 例外情况

### 4. 格式化回复
根据查询结果给出明确答复：
- **pending**: 退款申请已提交，正在审核中，预计1-3个工作日
- **processing**: 退款正在处理，预计X天到账
- **completed**: 退款已完成，请查收
- **rejected**: 退款被拒绝，原因是XXX

## 示例对话

**场景1：退款查询**

**用户**: 我的退款到哪了  
**助手**: 请提供您的订单号，我帮您查询退款进度。

**用户**: DD20260505123456  
**助手**: [调用 get_refund_status] 您的退款状态：
- 退款金额：299元
- 当前状态：处理中
- 预计到账：2026年5月8日

**场景2：退换货政策**

**用户**: 什么情况下可以退货  
**助手**: [调用 search_knowledge(policy)] 根据平台退换货政策：
- **7天无理由退货**：商品未使用、包装完好
- **15天质量问题退换**：存在质量缺陷
- **特殊商品**：食品、贴身用品等不支持退货

**场景3：保修咨询**

**用户**: 你们的保修政策是什么  
**助手**: [调用 search_knowledge(policy)] 我们的保修政策如下：
- **保修期限**: 自购买之日起1年
- **保修范围**: 非人为损坏的质量问题
- **不保修情况**: 人为损坏、私自拆修、进水等

## 注意事项
- 涉及退款的敏感问题需谨慎回答
- 如果退款被拒绝，清楚说明拒绝原因
- 政策咨询时提取关键点，不要大段复制
- 超出权限范围的问题（如加急处理），引导用户联系人工客服