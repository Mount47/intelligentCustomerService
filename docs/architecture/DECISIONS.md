# Architecture Decision Records

## ADR-001 单 Agent + Skill，而非多 Agent

售后流程的难点是业务约束、写操作安全和状态一致性，不是角色数量。统一 Agent harness 配合声明式
Skill 可以集中执行工具白名单、审计、token 记账和错误处理，减少多 Agent 消息传递带来的不可控状态。

## ADR-002 危险写操作不交给 LLM

LLM 只能在 Skill 允许的范围内调用只读工具。退款草稿、工单状态和人工升级由
`Skill.finalize` 根据数据库事实确定性执行。Guardrail 对危险工具调用默认拒绝。

## ADR-003 数据库约束承担幂等正确性

Redis 可以限流和削峰，但不承担退款唯一性的最终保证。消息和退款分别使用数据库唯一约束裁决并发，
失败事务通过 savepoint 回滚后回查赢家。这样 Redis 故障不会破坏资金类业务正确性。

## ADR-004 Session 精确绑定消息并原子抢占

每个 AgentSession 通过 `source_message_id` 绑定触发消息。worker 只允许
`queued/failed/timeout -> processing` 的条件更新成功者执行；终态 Session 的 broker 重投是 no-op。

## ADR-005 工单状态使用版本 CAS

worker 读取 Ticket 时保存状态和版本，最终以 `WHERE status=? AND version=?` 更新。CAS 失败时回滚回复、
退款和派生工单，避免并发 worker 用旧状态覆盖新状态。

## ADR-006 SSE 传输状态事件

浏览器使用带 Authorization Header 的 `fetch` 流读取 SSE。服务端推送完整会话视图的变化，
而不是暴露模型隐藏推理或逐 token 内容；断线后前端回退到鉴权轮询。

## ADR-007 评测分为确定性回归与真实语义评测

stub 评测进入 CI，用于验证意图规则、路由、状态、工具和合规断言。真实模型评测单独运行并归档报告，
不能用 stub judge 分数声称真实语义质量。
