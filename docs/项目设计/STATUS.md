# SupportFlow · 项目状态（总动态文件）

> **新会话从这里开始。** 这份文件是导航，不是真值。
> 真值优先级：**代码 + 测试 > git log > 本文档**。三者对不上，**信代码**。
> 红线：本文件超过一屏就是混进了不该放的东西——立刻外移到 `01-总体设计.md` / `DECISIONS.md` / `modules/`。
> 更新时机：**每个 milestone 收尾必更本文件 + 追加 `DECISIONS.md`**，不抄代码细节。
> 收尾三件套（ADR-9）：**测试通过 → 更新 STATUS → git commit**。测试没过不提交。

---

## 当前阶段
**M1.5 完成** — Agent 脊椎在进程内端到端跑通（stub LLM + 兜底 Skill），6 passed。

## 上个完成项
- **M1.5 走通骨架**：`app/llm`(LLMClient抽象+stub) + `app/agent`(context契约/state_machine/intent_classifier/skill_router/guardrails/agent_core harness) + `app/skills/general` + tests/test_agent_skeleton（4 passed）。证明：意图规则→路由→plan→harness loop→finalize→guardrails→状态机；token 记账链路通
- **M1 骨架**：FastAPI + core + db(探针) + /health + celery_app + deploy + tests/test_health（2 passed）
- 01 设计 6 项修正（ADR-8）

## 当前在做
- （M1.5 收尾）— 待启动 M2

## 下一步
- M2：11 张表 SQLAlchemy models（含 refund 双唯一约束/token 字段/消息幂等键）+ init_db + seed_data
- 之后 M3：services + tools（按 ToolResult 契约），接入 AgentCore 的 tool_registry

## 验证说明
- 已验证：py 编译全过；venv 跑全套 6 passed（health 结构 + agent 脊椎/意图/状态机/guardrails）
- 未验证：docker compose 实际拉起（本机未跑 docker）；真实 LLM 适配器（M4）；DB/Redis 真实连通（M2 起容器后验）

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
| M2 | 11 张表 + seed_data | ⬜ |
| M3 | Service + Tool + tool_calls 日志 | ⬜ |
| M4 | 状态机 + AgentCore（规则意图/路由） | ⬜ |
| M5 | RefundSkill（三层幂等）+ LogisticsSkill + guardrails | ⬜ |
| M6 | Celery + 轮询闭环 | ⬜ |
| M7 | 评测集（30+5）+ run_eval | ⬜ |
| M8 | 压测 + README | ⬜ |
| M9 | 并发幂等测试 + pytest + tracing | ⬜ |

## 导航
- 稳定设计：`01-总体设计.md` ✅（架构/Agent契约/LLMClient/状态机/表/幂等/guardrails）
- 决策记录：`DECISIONS.md`
- 硬模块细节：`modules/`（按需建，仅幂等/状态机/异步/评测）
- 原始需求：`../项目需求介绍/项目需求.md`
