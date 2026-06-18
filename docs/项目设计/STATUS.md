# SupportFlow · 项目状态（总动态文件）

> **新会话从这里开始。** 这份文件是导航，不是真值。
> 真值优先级：**代码 + 测试 > git log > 本文档**。三者对不上，**信代码**。
> 红线：本文件超过一屏就是混进了不该放的东西——立刻外移到 `01-总体设计.md` / `DECISIONS.md` / `modules/`。
> 更新时机：**每个 milestone 收尾必更本文件 + 追加 `DECISIONS.md`**，不抄代码细节。
> 收尾三件套（ADR-9）：**测试通过 → 更新 STATUS → git commit**。测试没过不提交。

---

## 当前阶段
**M7 完成** — 评测集(30常规+5对抗) + run_eval，stub 全指标达标，47 passed。

## 上个完成项（M7）
- **评测（§16/§23.3）**：`app/eval/test_cases.json`(30 常规 + 5 对抗) + `metrics.py` + `run_eval.py`（默认 stub 可复现，`--real` 用 .env 真模型）
  · 流程类：intent/skill/state/handoff 准确率 + tool_call_success_rate
  · 质量类：policy_compliance / forbidden_phrase_block / high_risk_handoff / resolution_rate
  · 成本类：avg_tokens / avg_cost per session
  · stub 跑分：流程+合规指标全 1.0，resolution 0.514
- GeneralSkill 补行为：human_handoff/product_complaint → 转人工（情绪/投诉优先转人工）
- tests/test_eval（2 passed，全套 47）

## 更早完成项（M6）
- **异步 API 闭环（§10/§14/ADR-10）**：
  · `POST /api/chat/message`：消息幂等去重 + 落库 + 建 session(queued) + 入队，立即返回（不阻塞）
  · `GET /api/chat/session/{id}`：task_status + latest_reply + **steps 时间线**(意图→技能→工具→结果) + token 视图
  · `workers/runner.run_agent_session`（纯函数，不依赖 celery，可测）+ `agent_tasks` celery 包装；按 .env 选 provider
  · `schemas/chat`、`services/chat_service`、`api/chat`（_dispatch 可 monkeypatch）；schema 用 Optional 兼容 3.9/3.11
- tests/test_chat_api（5 passed，全套 45）：入队/幂等不重复入队/轮询/worker 真处理(退款→completed、高风险→need_human)

## 更早完成项（M5）
- **两个业务 Skill（声明式，ADR-8）**：`skills/refund_handling` + `skills/logistics_exception`（各 handler + instructions.md），注册进 SkillRouter
  · 安全设计：LLM 在 loop 中**只读**取信息，**退款写操作与决策由 finalize 确定性执行**（不可逆动作不交给 LLM）
  · 退款：低风险自动草稿 / 高风险→草稿(pending_human)+升级工单+转人工 / 缺单号索取 / 非本人转人工
  · 物流：正常播报 / 48h无更新建催件工单 / 签收未收到转人工
  · finalize 契约加 tool_ctx；状态机加 created→need_human（升级工单）
- tests/test_skills（7 passed，全套 40）

## 更早完成项（M4）
- **M4·loop 执行**：`agent_core` harness loop 真执行工具（tool_use→execute→结果回灌→end_turn）+ 白名单越权拦截 + token 跨轮累计；`HybridIntentClassifier`(规则+LLM兜底)；`handle(ctx, tool_ctx)` 接 ToolContext。tests/test_agent_loop（3 passed）
- **M4·适配层（ADR-11）**：`claude_adapter`(anthropic) + `openai_compat_adapter`(GPT/DeepSeek/Qwen/vLLM/Ollama) + `registry`(按 LLM_PROVIDER 选)；`Msg` 扩展 tool_call_id/tool_calls；SDK 延迟导入；config 加 LLM_MAX_TOKENS/LLM_THINKING；.env 加示例。tests/test_llm_adapters（5 passed）

## 更早完成项
- **M3 services+tools**：6 个 service（order/logistics/refund/ticket/sla/knowledge）+ 5 个 tool 模块(14 工具) + `tools/base`(ToolResult + ToolRegistry 自动写 agent_tool_calls) + `tools/registry`；接进 `AgentCore.tool_registry`（据 SkillPlan.allowed_tools 取 specs）
  · **退款三层幂等**(refund_service)：层1 客户端 idempotency_key + request_hash、层2 business_key 去重、层3 DB 唯一约束兜底(捕 IntegrityError 回查)；高风险→pending_human 不自动退
  · tests：refund_idempotency 5 + services 6 + tools 4 = 15 passed（全套 25）
- **M2 数据层**：11 表 + init_db + seed_data（4 passed）
- **M1.5 骨架**：agent 脊椎（4）；**M1**：FastAPI 骨架（2）

## 当前在做
- （M7 收尾）— 进 M8 压测+README

## 下一步
- M8：Locust 压测 `POST /chat/message`(接入层削峰，P95<300ms/入队成功率>99%) + README（项目介绍/架构/状态机/Skill/工具/幂等/运行·测试·评测·压测/亮点/后续）
- 之后 M9 并发幂等(postgres 真并发) / M10·M11 前端对接

## 本地运行（无需 docker/redis/celery）
- `AGENT_DISPATCH=thread`：POST 用后台线程跑，轮询闭环照常（本地只需 uvicorn+sqlite）
- `scripts/demo_local.py`：连服务器都不用，sqlite 直接跑 Agent，打印 回复/思考步骤/token
  · stub：`DATABASE_URL=sqlite:///./_demo.db LLM_PROVIDER=stub python -m scripts.demo_local`
  · 真模型：设 LLM_PROVIDER/LLM_MODEL/key/base_url（见脚本 docstring）
- 已本地实跑 demo（stub）：低风险退款/高风险转人工/物流催件/兜底 四场景行为正确

## 下一步（二选一，看你优先级）
- **A 真实联调**：用真 .env（Claude/DeepSeek key）+ `docker compose up` 端到端跑一次（需你在终端跑 docker；我没法跑）。这是首次验证"真实模型+真实PG+异步削峰"
- **B M7 评测**：test_cases.json(30常规+5对抗) + run_eval（intent/skill/tool/handoff/状态机准确率 + guardrails 对抗指标）
- 之后 M8 压测+README / M9 并发幂等(postgres) / M10·M11 前端对接（steps 时间线已就绪）

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
| M4 | 状态机 + AgentCore + LLM适配层 + loop执行 | ✅ |
| M5 | RefundSkill（三层幂等）+ LogisticsSkill + guardrails | ✅ |
| M6 | Celery + 轮询闭环 | ✅ |
| M7 | 评测集（30+5）+ run_eval | ✅ |
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
