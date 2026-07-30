# SupportFlow 总体设计

## 目标与边界

SupportFlow 面向电商售后场景，把订单查询、物流异常、退款申请和人工升级组织成可审计的业务工作流。
系统不是开放域聊天机器人：LLM 负责语义理解、只读工具选择和自然语言表达，订单归属、退款风控、
二次确认、幂等写入和工单状态变更由确定性代码与数据库约束负责。

当前交付边界：

- 自动处理订单查询、普通物流查询、催件以及低风险退款申请；
- 高金额、特殊商品、超售后期、投诉和签收未收到进入人工队列；
- “退款”只创建内部退款申请，不代表已经调用支付渠道完成资金原路退回；
- SSE 推送会话状态与执行轨迹，不输出模型隐藏思维链，也不是逐 token 生成流；
- 压测衡量 API 落库和任务入队能力，真实 LLM 吞吐由 worker 数量和 provider 配额决定。

## 组件

1. Vue 3 前端
   - `/login`：换取短期 Bearer Token；
   - `/chat`：用户会话、状态流、工具审计摘要；
   - `/admin`：工单、会话、SLA、成本、质量与队列指标。
2. FastAPI
   - 鉴权、RBAC、IDOR 防护、限流、消息幂等；
   - 消息落库后立即投递 Celery，本地可切 thread 调度。
3. AgentCore
   - 规则快路 + LLM 意图兜底；
   - Skill 白名单工具循环；
   - Skill.finalize 执行确定性业务决策；
   - Guardrails 与状态机收口。
4. PostgreSQL
   - 保存订单、工单、对话、Session、退款、工具审计、状态历史、SLA 和质量记录；
   - 唯一约束与事务承担幂等正确性。
5. Redis/Celery
   - Redis 提供 broker、结果后端、接入限流和队列深度；
   - Celery 使用 late ack、有限重试、软硬超时和低预取。

## 核心请求序列

```text
POST /api/chat/message
  -> 校验 Bearer 身份与资源归属
  -> 固定窗口限流
  -> (user_id, client_message_id) 幂等落库
  -> 建立 AgentSession(source_message_id, queued)
  -> 投递 session_id

worker
  -> 原子 claim queued/failed/timeout -> processing
  -> 精确读取 source_message_id
  -> 恢复 Ticket 状态、待确认动作与历史
  -> Intent -> Skill.plan -> read-only tool loop -> Skill.finalize
  -> Ticket version CAS + 业务副作用同事务提交
  -> completed / waiting_user_input / need_human / failed
```

## 退款安全模型

- API 只接受认证上下文中的用户身份；
- Tool harness 强制以 Session 中的 `user_id/order_id` 覆盖模型参数；
- 低风险退款必须经过持久化的 `pending_action` 与明确确认；
- 高风险条件由订单金额、商品类型和签收时间确定性计算；
- LLM 工具循环禁止执行退款、转人工和状态变更等危险写工具；
- 退款请求由客户端幂等键、请求参数指纹、业务键和数据库唯一约束防重复；
- 同订单进行中退款另有会话级去重，避免跨轮重复申请。

## 可观测与评测

- `X-Trace-ID` 从 API 透传到 Celery worker；
- 每次工具调用记录脱敏后的输入、输出、成功状态和耗时；
- Session 记录模型、token、估算成本、处理时间和失败原因；
- 运行时指标覆盖队列深度、状态分布、SLA、解决/转人工率；
- 离线评测用于确定性流程回归；真实模型评测另行输出带 provider、模型、时间和成本的报告。
