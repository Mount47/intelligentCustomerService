# SupportFlow · 项目状态（总动态文件）

> **新会话从这里开始。** 这份文件是导航，不是真值。
> 真值优先级：**代码 + 测试 > git log > 本文档**。三者对不上，**信代码**。
> 红线：本文件超过一屏就是混进了不该放的东西——立刻外移到 `01-总体设计.md` / `DECISIONS.md` / `modules/`。
> 更新时机：**每个 milestone 收尾必更本文件 + 追加 `DECISIONS.md`**，不抄代码细节。
> 收尾三件套（ADR-9）：**测试通过 → 更新 STATUS → git commit**。测试没过不提交。

---

## 🧭 新会话开局清单（按序读，30 秒进入状态）
1. **本文件「当前阶段 / 下一步 / 已知坑」** —— 做到哪、接着干啥。
2. **`01-总体设计.md`** —— 架构 + Agent 执行链路契约(§5)/状态机(§7)/表(§8)/三层幂等(§9)/guardrails(§11)。
3. **`DECISIONS.md`** —— 13+ 条 ADR，每个决策的"为什么"。
4. **`../项目运行/运行手册.md`** —— 怎么跑/配置/排查、回答不准去哪改。
5. **`git log --oneline`** —— 客观进度。
> 真值优先级：**代码+测试 > git log > 文档**，对不上信代码。
> 铁规：①LLM provider 可插拔、模型走 `.env` ②harness 统一跑 ReAct loop，Skill 声明式、写操作由 finalize 确定性执行 ③退款三层幂等 ④**docs/ 不自动提交** ⑤每里程碑测试过后只提交代码。

---

## 当前阶段
**智能层加固完成** — 结构化意图 + 退款确认流 + 硬边界 + 订单查询 Skill，**104 passed**。
> 真实端到端：百炼 qwen-plus（裁判 qwen-max）`--real --judge-real`：流程指标全 1.0、对抗全拦、Judge 综合 4.57/5。详见 ADR-15~21。
> 注：docs/ 不自动提交（用户要求）；STATUS 仍在工作区维护。

## 本会话新增（代码已提交；决策见 DECISIONS ADR-15~21）
- **结构化意图**(ADR-16)：Intents→str-Enum + IntentResult(极性/动作/置信/需确认)；关键词只召回不触发；参数绑定防越权(IDOR)
- **退款确认流**(ADR-15)：refund_request 低风险→`waiting_user_confirm`+pending_action，确认才建草稿；`parse_confirmation` 专用 parser(fail-safe，治"不确定"误判)；cancel/inquiry 闭环
- **硬边界 guard 链**(ADR-17)：scope 硬闸(超范围固定拒答)/低置信澄清/确认态 parser，路由前确定性短路
- **语义层起步**(ADR-18)：规则快路 + 召回不确定带历史 defer LLM；退款召回口语化"退X"补全
- **会话级幂等**(ADR-19)：退款 get_active_refund + 物流催件 get_open_ticket，防同订单跨轮重复
- **评测两层**(ADR-20)：LLM-as-Judge(盲评/锚定/可换模型 JUDGE_MODEL)；实测指出订单查询短板→ **OrderQuerySkill**(只读播报)
- **P1 对话记忆**(ADR-21)：runner 加载工单历史进 ctx.history
- 早期：#5 上下文注入+参数绑定、#8 退款会话幂等、前端打通(dev mock bug)、数据库分层(ADR-14)

## 加固计划（让项目更 solid/可用，按 ROI）
1. ✅ 真实成本折算（pricing.py + TokenAcct，缓存读打折，stub→0）
2. ✅ Worker 健壮性（failed/timeout 分标 + retry_count + celery 重抛重试，线程/测试不崩）
3. ✅ 订单可用性（chat_service.resolve_order_from_text：文本提取订单号/id 校验归属；多轮补 ticket.order_id；前端示例对齐 seed）
4. ✅ 真实 LLM 端到端（百炼 qwen-plus）+ --real 评测（含 LLM-as-Judge 第二层）
5. ⬜ P2 事实接地（order_query 主动查 get_user_orders）/ P3 偏好 / P4 情景摘要
6. ⬜ Agent 上下文注入（order_id/user_id 进 system，修工具调用 FAILED；见根因分析）
7. ⬜ 可选：向量 RAG / 质检 Skill / SLA 监控(sla_records 只写不查) / Alembic / 删 quality_reviews 空表

## 上个完成项（M9）

## 上个完成项（M9）
- **并发幂等硬验**：`tests/test_concurrency.py` 8 线程 barrier 同发同一退款 → 断言只成一条 + 全拿同一 id + 仅一个 created（其余走 IntegrityError 回查/dedup）。默认文件 sqlite 验逻辑，`TEST_DATABASE_URL=postgres` 跑真并发
- **tracing**：`observability/tracing.py`(trace_id contextvar + TraceFilter)；日志格式加 `[trace_id]`；runner 每会话 `new_trace_id()` 串全链路
- tests/test_tracing(2) + test_concurrency(1)，全套 54
- 期间还补：CORS 中间件、seed 场景数据集+对照表、前端翡翠主题、前端对接 camelCase 接口

## 更早完成项（前端视图增强）

## 上个完成项（前端视图增强）
- 重写 `frontend/src/styles/base.css` 设计系统：翡翠绿+暖米纸面、衬线标题+等宽数字、渐变网格背景+微噪点、卡片错峰入场、思考时间线逐格点亮+处理脉冲；Element Plus 主题对齐
- ChatView/AdminView 共用该系统，无需改结构即全面换皮；AdminView 图表配色对齐主题
- 后端契约不变；`vue-tsc -b` typecheck 通过

## 更早完成项（前端对接接口，ADR-13）

## 上个完成项（前端对接接口，ADR-13）
- `CamelModel`(camelCase 序列化) + 重写 schemas(chat/admin/common)对齐 `frontend/src/api/types.ts`
- 会话视图升级为完整 `ChatSession`(messages + steps + toolCalls + tokenUsage)；task_status 内部→前端枚举映射
- 新增 admin 查询接口：`/api/admin/{metrics,tickets,tickets/{id},sessions,sessions/{id}}`(metrics 对齐 AdminMetrics + 削峰超集)
- 同步改 chat 接口响应为 camelCase；demo_local/run_eval/测试适配新形状
- tests/test_admin +1(tickets/sessions)，全套 51；前端设 `VITE_USE_MOCK=false` 即可连

## 更早完成项（M8）
- **观测接口**（压测/管理端共用，ADR-12）：`app/observability/metrics.compute_metrics` + `GET /api/admin/metrics`（状态分布 + Redis 队列深度 + agent 表现 + token/成本）
- **压测**：`loadtest/locustfile.py`（压 POST /chat/message，校验入队成功，读写混合，三档）；stub 加 `STUB_DELAY_MS` 模拟时延（削峰可见）
- **README**：项目介绍/架构/状态机/三层幂等/表/API/运行(本地+docker)/切模型/测试/评测/压测边界/亮点/后续
- **CI 烟雾测**（不依赖 docker）：metrics 计算 + /admin/metrics 端点 + 接入路径 30 连发全入队。tests/test_admin（3 passed，全套 50）
- `.gitignore` 补 `*.db`

## 更早完成项（M7）
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
- （前端对接接口 收尾）— 后端接口已就绪，可联调 Vue 前端

## 下一步（看优先级）
- **前端联调**（你跑）：`frontend/` 设 `VITE_API_BASE_URL=http://localhost:8000/api`、`VITE_USE_MOCK=false`，起后端(uvicorn thread 模式) + `npm run dev`，看用户端提问/思考过程、运维端指标/工单/会话
- **本地实压**（你跑）：docker compose + locust + 看 /admin/metrics 削峰
- **M9** 并发幂等真测（postgres 多线程）+ tracing
- 前端两端的 Vue 视图增强（ChatView/AdminView）按需我可协助

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
- ✅ **真并发幂等实测已通过**（2026-06-23，docker postgres:16 + `TEST_DATABASE_URL=PG` 跑 test_concurrency：8 线程同发只成 1 条；唯一约束并发拒重实测）。详见 ADR-14。
- ✅ 真实 LLM 端到端（百炼 qwen-plus / 裁判 qwen-max，`--real --judge-real`）
- 未验证（可补）：docker compose **全栈**(api+worker+redis) 一键拉起端到端
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
| M8 | 压测 + README + /admin/metrics | ✅ |
| M9 | 并发幂等测试 + pytest + tracing | ✅ |
| M10 | 前端·用户聊天端（Vue3，提问→答复，思考过程时间线，轮询） | ⬜ |
| M11 | 前端·管理员端 = **运维监测端**（处理过程/指标/压测削峰可视化） | ⬜ |

> 前端（ADR-10/12）= Vue3，已在 `frontend/`（用户脚手架）。admin 端 = 运维监测端。
> 后端观测接口压测/监测共用：M8 建 `/api/admin/metrics`；`/api/admin/sessions`、`/api/admin/tickets` 在 M11 前补。
> 业务逻辑已完结，后续只加观测接口 + 前端。

## 导航
- 稳定设计：`01-总体设计.md` ✅（架构/Agent契约/LLMClient/状态机/表/幂等/guardrails）
- 决策记录：`DECISIONS.md`
- 硬模块细节：`modules/`（按需建，仅幂等/状态机/异步/评测）
- 原始需求：`../项目需求介绍/项目需求.md`
