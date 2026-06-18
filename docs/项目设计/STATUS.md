# SupportFlow · 项目状态（总动态文件）

> **新会话从这里开始。** 这份文件是导航，不是真值。
> 真值优先级：**代码 + 测试 > git log > 本文档**。三者对不上，**信代码**。
> 红线：本文件超过一屏就是混进了不该放的东西——立刻外移到 `01-总体设计.md` / `DECISIONS.md` / `modules/`。
> 更新时机：**每个 milestone 收尾必更本文件 + 追加 `DECISIONS.md`**，不抄代码细节。
> 收尾三件套（ADR-9）：**测试通过 → 更新 STATUS → git commit**。测试没过不提交。

---

## 当前阶段
**M3 完成** — services + tools + 三层幂等实现就位，25 passed。

## 上个完成项
- **M3 services+tools**：6 个 service（order/logistics/refund/ticket/sla/knowledge）+ 5 个 tool 模块(14 工具) + `tools/base`(ToolResult + ToolRegistry 自动写 agent_tool_calls) + `tools/registry`；接进 `AgentCore.tool_registry`（据 SkillPlan.allowed_tools 取 specs）
  · **退款三层幂等**(refund_service)：层1 客户端 idempotency_key + request_hash、层2 business_key 去重、层3 DB 唯一约束兜底(捕 IntegrityError 回查)；高风险→pending_human 不自动退
  · tests：refund_idempotency 5 + services 6 + tools 4 = 15 passed（全套 25）
- **M2 数据层**：11 表 + init_db + seed_data（4 passed）
- **M1.5 骨架**：agent 脊椎（4）；**M1**：FastAPI 骨架（2）

## 当前在做
- （M3 收尾）— 待启动 M4

## 下一步
- M4：真实 LLM 适配器（ClaudeAdapter，anthropic SDK，.env 选 provider）+ LLMIntentClassifier 兜底；harness loop 实跑工具执行（ToolContext 进 loop）
- 之后 M5：RefundHandlingSkill + LogisticsExceptionSkill（串通真实链路）

## 验证说明
- 已验证：venv 全套 **25 passed**（含退款三层幂等、工具审计落库、幂等贯通工具层）
- 真并发(多线程)退款测试 → M9 用 postgres 补（sqlite 难真并发，现仅验顺序幂等 + IntegrityError 回查路径逻辑）
- 未验证：docker compose 拉起；真实 LLM（M4）；真实 PG 连通
- 注：前端目录(frontend/，Vite)由用户脚手架，M10/M11 才做，不进 M3 提交

## 已定 LLM
- provider 抽象 `LLMClient`，首个适配器 = Claude `claude-opus-4-8`（anthropic SDK），可换
- 意图 = 规则快路 + LLM 兜底
- agent loop = 原生工具调用循环 + harness 闸门（guardrails/状态机/幂等）

## 已知坑 / 待确认（剩小项，倾向默认）
- [ ] `idempotency_key`：客户端可传，不传 API 按 user+order+content_hash 兜底〔默认〕
- [ ] 退款阈值：配置化放 config（500 / 7天）〔默认〕
- [ ] 包管理：uv + pyproject〔默认〕

---

## Milestone 进度
| M | 内容 | 状态 |
|---|---|---|
| M1 | 骨架 + docker-compose | ✅ |
| M2 | 11 张表 + seed_data | ✅ |
| M3 | Service + Tool + tool_calls 日志 | ✅ |
| M4 | 状态机 + AgentCore（规则意图/路由） | ⬜ |
| M5 | RefundSkill（三层幂等）+ LogisticsSkill + guardrails | ⬜ |
| M6 | Celery + 轮询闭环 | ⬜ |
| M7 | 评测集（30+5）+ run_eval | ⬜ |
| M8 | 压测 + README | ⬜ |
| M9 | 并发幂等测试 + pytest + tracing | ⬜ |
| M10 | 前端·用户聊天端（Vue3，思考过程时间线，轮询） | ⬜ |
| M11 | 前端·管理员端（看板/工单/会话/审计） | ⬜ |

> 前端（ADR-10）= Vue3 + Element Plus/Naive UI，排在后端核心(M3~M6)之后；需后端补 `GET /chat/session` 的 steps/timeline + admin 端点。

## 导航
- 稳定设计：`01-总体设计.md` ✅（架构/Agent契约/LLMClient/状态机/表/幂等/guardrails）
- 决策记录：`DECISIONS.md`
- 硬模块细节：`modules/`（按需建，仅幂等/状态机/异步/评测）
- 原始需求：`../项目需求介绍/项目需求.md`
