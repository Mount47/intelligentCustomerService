# 决策记录 (ADR) · 追加式，不重写

> 每条格式：编号 / 日期 / 决策 / 为什么 / 否决了什么。面试"为什么这么做"的答案库。

## ADR-1 · 2026-06-17 · v1 范围只做核心子集
**决策**：v1 只做退款 + 物流两条链路 + 4 个硬模块（幂等/异步闭环/状态机/评测+guardrails），砍掉投诉/发票/质检 Skill、MCP、向量 RAG、前端。
**为什么**：范围砍一半，把时间砸在能讲透的硬模块上；全量 spec 每块都浅，核心讲不透。
**否决**：全量 spec 一次做完（demo 面广但深度不足）。

## ADR-2 · 2026-06-17 · 全程 mock LLM 〔已废弃 → 见 ADR-7〕
~~决策：意图=规则关键词，回复=模板，LLM 用 mock 桩。~~
**废弃原因**：用户明确要求接真实、可靠、复杂、可上线的 Claude，不要 mock。被 ADR-7 取代。
保留价值：`token_cost / prompt_tokens / cache_hit` 等记账字段结构仍要——但记的是真实 usage，不是占位。

## ADR-3 · 2026-06-17 · 部署只做本地 docker-compose
**决策**：一条命令拉起 FastAPI+PG+Redis+worker，README 写清启动；Dockerfile/compose/nginx 按可上云方式写但不实际上云。
**为什么**：本地能完整演示+压测，满足简历/面试；上云增量价值低、耗时。

## ADR-4 · 2026-06-17 · 退款三层幂等
**决策**：客户端 `idempotency_key`（防重复点击/重试）+ 服务端 `business_key=hash(user_id+order_id+action+reason_normalized)`（防 Agent 重试）+ DB `UNIQUE` 约束（并发兜底）。Redis SETNX 仅短期防抖锁，不作正确性保证。
**为什么**：正确性必须由 DB 唯一约束+事务保证，不能靠锁；这是高并发故事的命门。
**否决**：单靠 Redis 锁（进程崩溃/锁过期会漏）。

## ADR-5 · 2026-06-17 · 异步用轮询拿结果
**决策**：`POST /chat/message` 落库+入队立即返回；用户 `GET /chat/session/{id}` 轮询 task_status 拿 latest_reply。SSE/WebSocket 列后续。
**为什么**：第一版必须跑通完整闭环可演示；轮询最简单可靠。

## ADR-6 · 2026-06-17 · 文档按变化频率分 3 层
**决策**：STATUS.md（总动态/每模块收尾更/一屏）+ DECISIONS.md（追加式）+ 01-总体设计.md（稳定）+ modules/（仅硬模块）。代码+测试+git 为真值，文档为导航。
**为什么**：避免单文件膨胀吃上下文、避免文档抄代码后过时；细分不按模块铺开以防文件爆炸。

## ADR-7 · 2026-06-17 · 接真实 LLM，provider 可插拔（取代 ADR-2 的 mock）
**决策**：接真实、可靠、可上线的 LLM；**不锁定单一厂商**，抽象 `LLMClient` 接口，provider 配置化可切换。
- **抽象层**：统一接口（chat / tool-calling / 流式 / usage 上报），适配器实现 Claude(anthropic SDK)、OpenAI 兼容(OpenAI/Qwen/DeepSeek/本地 vLLM)、Ollama 等；模型与 key 全放 config/.env，运行时按 `LLM_PROVIDER` 选。
- **能力下限**：所选模型必须支持 function/tool calling（agent loop 依赖它）；不支持的 provider 退化为"LLM 规划→harness 执行"的受限模式。
- **agent loop**：原生工具调用循环（ReAct），harness 在每次工具调用处插 guardrails+状态机+权限/幂等闸门；危险工具（create_refund_draft）不盲目执行，走高风险/人工确认闸门。
- **guardrails 作用于真实 LLM 输出**：结构化输出约束 + 系统提示 + 输出后校验三层；对抗样例评测在真实输出上跑。
- **token/cost**：记录真实 usage（prompt/completion/cache_read/total/cost，按 provider 价目折算）；支持 prompt caching 的 provider 缓存系统提示+政策文档前缀降本。
- **Worker 限流**：LLM 调用在 worker 池异步消费、并发受限，队列削峰；API 接入层不阻塞。
**为什么**：用户要真实/复杂/可上线，且不限定 Claude；provider 抽象让模型可换、可降本，是工程亮点。真实工具调用循环是脊椎，guardrails 必须真拦得住会乱承诺的 LLM。
**已定**：①默认/首个适配器 = Claude `claude-opus-4-8`（anthropic SDK），抽象层保留可换其他 provider；②意图 = 规则快路 + LLM 兜底（明确关键词走规则，模棱才调 LLM）。
**注**：上一轮"非 ReAct、纯确定性管道"的判断作废（mock 假设下的结论）。

## ADR-8 · 2026-06-17 · 01 设计的 6 项修正（harness 统一 loop + 两类幂等）
对 `01-总体设计.md` 的契约修正，逐条理由：
1. **退款唯一约束 `UNIQUE(user_id, idempotency_key)` + 加 `request_hash`**：key 在用户维度唯一更安全；request_hash 做参数指纹判同 key 是否同请求（冲突→409）。
2. **消息幂等 vs 退款业务幂等分离**：不共用 `hash(content)`。消息幂等用 `UNIQUE(user_id, client_message_id)` 防重复处理；退款业务幂等三层独立。理由：两者目的不同，混用会让"换句话描述同一退款"绕过幂等或误判冲突。
3. **Skill 不自带 ReAct loop，harness 统一执行**：Skill 只产 `SkillPlan`（系统提示+工具白名单+完成策略）+ `finalize→Decision`；AgentCore 跑唯一 loop。理由：guardrails/状态机闸门/工具审计/token 记账集中一处，不散落各 Skill、不重复实现、不漏闸门。
4. **`tool_results` → `tool_call_records`（含 input/result/latency/error）**：便于 tracing 和落 agent_tool_calls。
5. **v1 做 metrics/eval，不做完整 QualityReviewSkill**：quality_reviews 表留结构不写入。
6. **模型名不写死**：`LLM_MODEL`/`LLM_PROVIDER` 全走 .env，切模型=改配置不动代码。
**为什么**：用户审阅 01 时提出；3 是对 ADR-7 "agent loop" 的实质收敛——循环归 harness，Skill 只声明。

## ADR-9 · 2026-06-17 · 每个里程碑完成且测试通过后必须 git 提交
**决策**：每个 milestone（M1、M2…）**完成并且测试通过**后，立即 git commit；提交信息写清该阶段交付了什么。直接提到默认分支 `main`，保持线性的里程碑历史（solo 学习项目，暂不用 feature 分支/远程）。
**为什么**：用户要求。让"项目状态活在 repo（git log）里"——git log 是客观进度真值（呼应 ADR-6 文档分层：代码+测试 > git log > 文档）。每阶段一提交便于回溯、对照 STATUS、面试时讲演进。
**怎么做**：milestone 收尾 = 跑测试通过 → 更新 STATUS → `git commit`（三件套绑定）。提交信息结尾带 `Co-Authored-By: Claude Opus 4.8`。测试没过不提交。

## ADR-10 · 2026-06-18 · 加前端两端（推翻 ADR-1 "v1 不做前端"）
**决策**：做用户聊天端 + 管理员端两个前端，**Vue 3 + Element Plus / Naive UI**。
- **用户端 `/chat`**：输入问题→消息气泡→回复；**"思考过程"= Agent 步骤时间线**（识别意图→路由技能→工具调用→风险判断→生成回复），数据来自 `GET /chat/session` 的 `current_intent/skill/state` + `agent_tool_calls`，真实可追溯、零额外成本（LLM 思考摘要作为可选高级视图，后续）；轮询对齐后端（ADR-5），SSE 后续。
- **管理员端 `/admin`**：概览看板（工单量/解决率/转人工率/平均 token 成本/P95 延迟）、工单列表+详情（状态机时间线/消息）、会话详情（token/cost + agent_tool_calls 审计）；质检页留空（QualityReviewSkill 后续）。
- **时机**：后端核心 M3~M6 跑通（有真实 /chat/message、/chat/session、admin metrics）后再做前端里程碑，避免对着 mock 返工。
**影响后端**：`GET /chat/session` 响应需带 `steps/timeline`（意图/技能/工具调用进展）供前端点亮；需补 admin 端点（metrics/tickets/sessions/tool_calls）。
**为什么**：用户要求页面精美 + 可输入得回复 + 看简单思考过程 + 管理员端；前端正好把异步状态/Agent 决策轨迹/成本可视化，是展示亮点。

## ADR-11 · 2026-06-18 · LLM 适配器广覆盖：2 个适配器覆盖全部主流模型
**决策**：`LLMClient` 抽象下只需 **2 个适配器**即覆盖 Claude/GPT/DeepSeek/Qwen/本地：
- `ClaudeAdapter`：anthropic SDK（Claude 自有协议；adaptive thinking、prompt caching、usage）。
- `OpenAICompatAdapter`：openai SDK + `base_url`，覆盖 **GPT / DeepSeek / Qwen / vLLM / Ollama**（它们都暴露 OpenAI 兼容的 chat.completions + function calling）。
- `registry.build_llm_client` 按 `.env` 的 `LLM_PROVIDER` 选：`claude`→Claude；`openai/openai_compat/deepseek/qwen/gpt/vllm/ollama`→OpenAICompat；`stub`→离线桩。
- 切模型/厂商 = 改 `.env`（`LLM_PROVIDER` + `LLM_MODEL` + key + `OPENAI_BASE_URL`），不动代码。
- 工具调用归一化为 provider 无关的 `LLMResponse.tool_calls`；`Msg` 扩展 `tool_call_id/tool_calls` 以支持 agent loop 的工具结果回放。
- **SDK 延迟导入**（adapter 内构造 client 时才 import），离线/未装 SDK 也能单测，且 anthropic/openai 成可选依赖。
**为什么**：用户要求适配器兼容各种模型（Claude/GPT/DeepSeek/Qwen…）走 API；OpenAI 兼容协议是事实标准，一个适配器吃下大半生态，工程量最小、可降本切换。

## ADR-12 · 2026-06-18 · 管理员端 = 运维监测端；观测接口压测/监测共用
**决策**：管理员端定位为**运维监测端**——展示 ①Agent 处理详细过程(思考步骤) ②可观测指标(工单量/解决率/转人工率/token成本/延迟/**队列深度/状态分布**) ③**压测运行时信息(削峰可视化)**。
- 后端观测接口**一套两用**（压测观测 + 管理端 dashboard）：`GET /api/admin/metrics`（状态分布 + Redis 队列深度 + token/延迟聚合）、`/api/admin/sessions(/{id})`、`/api/admin/tickets(/{id})`；处理过程复用已有 `GET /chat/session/{id}` 的 steps。
- 业务逻辑（skills/tools/agent/三层幂等/状态机）**已完结**；后续新增主要是**观测接口 + 前端两端**，不再加业务。
- 落地顺序：**M8 先建 `/api/admin/metrics`**（压测削峰要用）；其余 admin 查询接口在 M11 前端对接时补齐。
- 压测“削峰可视化” = 运维端实时刷 `/api/admin/metrics` 的队列深度/状态分布；Locust 自身 P95/RPS 报告在其 web UI/CSV，按需嵌快照。
**为什么**：用户明确 admin 端是运维监测端且要展示压测信息；运行时指标与压测观测本就是同一组数据，接口一套两用，省一半活。
