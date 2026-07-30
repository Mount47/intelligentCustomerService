# SupportFlow · 电商售后智能客服工单 Agent

把高频售后咨询从「人工反复处理」变成「Agent 自动识别 → 查询 → 判断 → 处理 → 升级 → 记录 → 评估」的完整业务流程。不是 FAQ 问答机器人，而是带**业务流程、工具调用、工单状态机、人工升级、退款幂等、异步削峰、评测与 guardrails 对抗**的单 Agent 工程项目。

> 设计、ADR 与交付状态见 [`docs/architecture/`](docs/architecture/)。

## 解决什么问题
大促/高峰/日常售后中，退款、退货、物流、催发货、投诉等大量涌入时，人工响应慢、处理不标准、系统切换多、质检困难。SupportFlow 把退款 / 物流两条高频链路工程化、可上线、可压测、可评测。

## 核心能力
- **意图识别**：规则快路 + LLM 兜底
- **Skill 业务流程**：RefundHandling（低风险二次确认后建退款申请 / 高风险转人工）、LogisticsException（催件 / 签收未收到转人工）、OrderQuery（订单与商品明细）、General 兜底（投诉·要求人工 → 转人工）
- **工单状态机**：集中管理、非法转移抛异常、每次转移记日志
- **退款三层幂等**：客户端 idempotency_key + 服务端 business_key + DB 唯一约束兜底
- **Agent harness**：统一跑 LLM 工具调用循环（ReAct），危险写操作不交给 LLM，由 finalize 确定性执行
- **LLM 多 provider**：Claude / GPT / DeepSeek / Qwen / 本地 vLLM·Ollama，改 `.env` 即切，不动代码
- **异步闭环**：API 落库+入队立即返回，worker 异步消费，SSE 推送处理进度（异常退回轮询）
- **认证与 RBAC**：Bearer Token 从认证上下文确定用户身份；普通用户只能访问本人会话，管理接口仅 admin 可用
- **可观测**：agent_tool_calls 审计、token/cost 记账、运行时 metrics
- **评测 + guardrails 对抗**：47 个单轮/多轮评测回合，量化流程、转人工与合规拦截

## 系统架构（文字版）
```
用户 ──HTTP──▶ API 层(FastAPI)
                │ POST /chat/message → 落库+建session+入队，立即返回(不阻塞)
                │ GET  /chat/session/{id} → 轮询 task_status + latest_reply + steps时间线
                │ GET  /api/admin/metrics → 状态分布/队列深度/token/延迟
                ▼ enqueue(Redis)
            异步任务层(Celery worker / 本地 thread)
                ▼
            Agent 核心(harness 管道)
              意图(规则+LLM兜底) → SkillRouter → Skill.plan
              → harness 跑 ReAct loop（工具调用前后插 guardrails + 状态机闸门）
              → Skill.finalize（确定性业务决策/写操作）→ guardrails 回复校验 → 状态机转移
                ▼ 只经 tool/service
       工具层(16工具,统一ToolResult,自动审计) · 服务层(order/logistics/refund/ticket/sla/knowledge) · LLM抽象层(provider可插拔)
                ▼
            PostgreSQL(13张表) + 可观测落库
```

## 工单状态机
正常态：`created → intent_detected → info_required → info_collected → tool_executing → waiting_user_confirm → resolved_by_agent | need_human → resolved_by_human | rejected → closed`
异常态：`tool_failed / policy_conflict / user_angry / refund_high_risk / sla_timeout`
规则：转移集中在 `StateMachine`，非法转移抛 `InvalidStateTransition`；高风险禁止直达 `resolved_by_agent`，强制 `need_human` / `waiting_user_confirm`。

## 退款三层幂等
1. **客户端 `idempotency_key`** + `request_hash`（参数指纹）：防重复点击/重试；不传则据业务字段派生（非 hash(content)）
2. **服务端 `business_key`** = hash(user_id + order_id + action + normalized_reason)：防相同业务参数重放
3. **DB 唯一约束**：`UNIQUE(user_id, idempotency_key)` + `UNIQUE(business_key)`，捕 IntegrityError 回查赢家
此外，Skill 会检查同一用户/订单是否已有进行中退款，防跨轮换措辞重复申请。当前退款正确性**不依赖 Redis 锁**，完全由数据库唯一约束与事务保证；SETNX 只保留为未来可选防抖思路，尚未接入退款主链路。高金额/生鲜·定制/超售后期 → 不自动退款，建草稿(pending_human) + 升级工单转人工。

## 数据库表（13）
users / orders / order_items / logistics / refund_requests / tickets / ticket_messages / ticket_state_transitions / agent_sessions / agent_tool_calls / knowledge_docs / quality_reviews / sla_records。

## API
- `GET /health`、`GET /`、`GET /metrics`（Prometheus）
- `POST /api/auth/token`
- `POST /api/chat/message`、`GET /api/chat/session/{id}`、`GET /api/chat/session/{id}/stream`
- `GET /api/admin/metrics|tickets|sessions` 及工单/会话详情（需要 admin）
- 接口文档：`/docs`

---

## 运行

### 5 分钟秋招核心演示（推荐先跑）
```bash
python -m scripts.demo_recruitment
```
完全离线、无需 API Key/Redis/Celery/Docker；脚本用断言依次验证跨轮补槽、只读工具审计、退款二次确认、消息幂等、高风险转人工、物流异常和越权拦截。任一核心链路不符合预期会直接失败退出。

### 方式一：本地（无需 docker/redis/celery）
```bash
python3.11 -m venv .venv && . .venv/bin/activate && pip install -e .
# 一键体验（stub 模型，零依赖）
DATABASE_URL=sqlite:///./local.db LLM_PROVIDER=stub python -m scripts.demo_local
# 接真实模型（Qwen 百炼示例）
DATABASE_URL=sqlite:///./local.db LLM_PROVIDER=qwen LLM_MODEL=qwen-plus \
  OPENAI_API_KEY=sk-xxx OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  python -m scripts.demo_local
# 起 HTTP 服务（thread 调度，无需 Redis/Celery）
DATABASE_URL=sqlite:///./local.db python -m scripts.seed_data
DATABASE_URL=sqlite:///./local.db AGENT_DISPATCH=thread AUTH_SECRET=local-secret \
  uvicorn app.main:app --reload
# POST /api/auth/token 获取 Bearer Token；seed 的 demo 密码为 supportflow-user
```

### 方式二：docker-compose（完整栈：前端 + API + PostgreSQL + Redis + Celery worker）
```bash
cp .env.example .env   # 填 LLM key
docker compose -f deploy/docker-compose.yml up --build
docker compose -f deploy/docker-compose.yml exec api python -m scripts.seed_data
curl localhost:8000/health
```

### 切换模型（不动代码，改 .env）
| provider | LLM_PROVIDER | 关键变量 |
|---|---|---|
| Claude | `claude` | `ANTHROPIC_API_KEY`、`LLM_MODEL=claude-opus-4-8` |
| DeepSeek | `deepseek` | `OPENAI_API_KEY`、`OPENAI_BASE_URL=https://api.deepseek.com/v1`、`LLM_MODEL=deepseek-chat` |
| Qwen 百炼 | `qwen` | `OPENAI_API_KEY`、`OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`、`LLM_MODEL=qwen-plus` |
| 本地 vLLM | `vllm` | `OPENAI_BASE_URL=http://localhost:8000/v1`、`LLM_MODEL=...` |

## 测试
```bash
pytest -q          # 全套（2026-07-30 本地 196 passed）
```
CI 同时执行离线评测、前端生产构建、真实 Redis + Celery broker/worker 端到端验收，以及 PostgreSQL 下退款和消息接入各 16 线程的并发幂等硬验。

## 评测
```bash
python -m app.eval.run_eval          # stub（可复现，CI 友好）
python -m app.eval.run_eval --real --judge-real --confirm-paid-run \
  --cases app/eval/semantic_cases.json --output eval/results/real-report.json
```
输出流程类（intent/skill/state/handoff/tool）+ 质量类（policy_compliance / forbidden_block / high_risk_handoff / resolution）+ 成本类（avg_tokens/cost）。
**诚实边界**：仓库内默认报告使用 stub，验证规则意图识别、Skill 路由、工具调用、状态机流转、人工升级策略与 guardrails 合规的**正确性与覆盖度**；真实模型语义集与可选裁判链路已经接通，但会产生模型费用，需显式传入 `--confirm-paid-run`，不把未执行的付费结果冒充项目证据。

## 压测
```bash
# 1) docker compose 起全栈（.env 设 LLM_PROVIDER=stub，可设 STUB_DELAY_MS=100 让削峰可见）
# 2) seed 数据
# 3) 压接入层并同步归档 RPS/P95/P99 与 broker 队列曲线：
python -m scripts.run_loadtest_and_capture \
  --host http://localhost:8000 --users 50 --spawn-rate 10 --duration 30s
```
**压测边界（必读）**：压的是**消息接入 + 入队**能力（POST 落库+入队立即返回），不是 1000 个并发 LLM 推理。系统高并发体现在 API 接入、消息落库、任务入队与队列削峰；LLM 推理由 worker 池限流异步消费。目标：入队成功率 > 99%、错误率 < 1%、接入层 P95 < 300ms（绝对值依赖机器，本地仅作相对验证）。削峰可视化 = 压测时 `/api/admin/metrics` 的 `queue_depth` 随并发飙升后被 worker 抽干。

2026-07-27 本机实压（2 个 Celery worker 进程、Stub 100ms、50 用户、30 秒）：4396 请求、0 失败、整体 151.5 RPS / P95 68ms / P99 110ms，聊天 POST 125.0 RPS / P95 70ms / P99 110ms，broker 队列峰值 3089。原始 CSV 与摘要在 `loadtest/results/20260727-192419/`；这是单机接入/削峰基线，不代表真实 LLM 吞吐或生产容量。

## 项目亮点
- 退款三层幂等（并发只成一条）+ 危险写操作不交给 LLM 的安全设计
- LLM provider 抽象，一套代码适配 Claude/GPT/DeepSeek/Qwen/本地
- 异步队列 + Bearer SSE 进度推送 + Agent 执行轨迹可视化
- 评测含 guardrails 对抗样例，把「写了」变「测过了」
- token/cost 与运行时 metrics 可观测，压测/管理端共用

## 项目边界（秋招收口）
- **本项目重点**：Agent harness、状态机、确定性写操作、二次确认、退款幂等、并发与故障降级。
- **刻意不扩功能面**：不继续堆多 Agent、向量 RAG、MCP、大量低价值 Skill 或复杂基础设施。
- **真实生产仍需**：企业身份源、密钥轮换与 refresh token，更细粒度的人工客服组织/权限模型，队列告警与背压策略，以及接入企业监控平台；当前认证是项目内最小 HMAC Token，人工闭环是可演示的最小工作流，不冒充企业级 IAM/工单系统。
