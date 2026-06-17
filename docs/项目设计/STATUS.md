# SupportFlow · 项目状态（总动态文件）

> **新会话从这里开始。** 这份文件是导航，不是真值。
> 真值优先级：**代码 + 测试 > git log > 本文档**。三者对不上，**信代码**。
> 红线：本文件超过一屏就是混进了不该放的东西——立刻外移到 `01-总体设计.md` / `DECISIONS.md` / `modules/`。
> 更新时机：**每个 milestone 收尾必更本文件 + 追加 `DECISIONS.md`**，不抄代码细节。
> 收尾三件套（ADR-9）：**测试通过 → 更新 STATUS → git commit**。测试没过不提交。

---

## 当前阶段
**M2 完成** — 11 张表 models + init_db + seed_data 就位，10 passed。

## 上个完成项
- **M2 数据层**：`app/db/models.py`(11 表，退款双唯一约束 uq_refund_user_idem/uq_refund_business + request_hash、消息幂等 uq_msg_user_clientid、agent_sessions token/task_status 字段全) + `init_db.py` + `scripts/seed_data.py`(10用户/50订单/14物流/7政策/4异常) + tests/test_models（4 passed）
- **M1.5 走通骨架**：app/llm(抽象+stub) + app/agent(契约/状态机/意图/路由/guardrails/harness) + general skill（4 passed）
- **M1 骨架**：FastAPI + core + db探针 + /health + celery + deploy（2 passed）

## 当前在做
- （M2 收尾）— 待启动 M3

## 下一步
- M3：services（order/logistics/refund/ticket/sla/knowledge）+ tools（按 ToolResult 契约、自动写 agent_tool_calls）+ 接入 AgentCore.tool_registry
- 退款 service 落三层幂等实现（M3 起步，M5 串进 RefundSkill）

## 验证说明
- 已验证：venv 全套 **10 passed**；seed_data 指向临时 sqlite 跑通（计数正确）
- 代码约定：SQLAlchemy `Mapped[]` 注解用 `Optional[X]` 而非 `X|None`（后者 3.9 运行时 eval 报错；目标 3.11 也兼容）
- 未验证：docker compose 实际拉起；真实 LLM 适配器（M4）；真实 PG 连通（起容器后）

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
