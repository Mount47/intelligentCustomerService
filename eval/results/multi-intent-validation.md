# 第一阶段多意图验收记录

评测日期：2026-08-03。

## 已完成

- 后端全量测试：238 项通过，0 失败；另有 1 条第三方测试客户端弃用提醒。
- 前端生产构建：通过。构建工具提示部分代码块超过 500 kB，不影响本次功能正确性。
- 场景集结构校验：59 个场景、77 个对话轮次、6 个分类，校验通过。
- Stub 完整场景评测：71/77，通过率 92.21%，46 个关键轮次失败为 0。
- Stub 的新增多意图分类：10/10；业务边界、安全防护、多轮流程、异常恢复也均为 100%。
- 多意图自动化测试覆盖：订单查询+物流查询、物流查询+退款咨询、物流查询+退款申请、
  物流查询+退货申请、否定退款、投诉优先、两个写意图、确认夹带诉求、高风险、越权、
  跨订单和多个技能工具调用。
- 上一阶段 Qwen 基线评测：原50场景、67逻辑轮次 repeat=3 共201轮全部通过，
  关键失败0、偶发失败0；详见 `qwen-plus-scenario-final.md`。

Stub 剩余6个失败全部属于 `semantic_understanding` 的隐晦口语，固定回复 Stub 不具备真实模型的
语义兜底能力；关键失败为0，且不涉及新增多意图流程。

## 补充完成（2026-08-03 晚）

原先受外部条件阻塞的两项已全部执行完毕。

### 1. 扩展后59场景、77轮 Qwen 真实模型评测：通过

`qwen-plus`，77/77 轮通过，通过率 100%；关键轮次 46 个，关键失败 0；
模型调用 140 次，模型错误 0；总 token 79,422，成本约 $0.042。
轮次延迟均值 3,679 ms、p50 3,271 ms、p95 9,024 ms。
六个分类全部 100%：异常恢复 4/4、业务边界 15/15、多意图 10/10、
多轮流程 26/26、安全防护 8/8、语义理解 14/14。

Stub 评测中失败的 6 条隐晦口语场景，在真实模型下全部通过，
印证此前判断——该缺口来自固定回复 Stub，不是链路缺陷。
报告：`qwen-plus-multi-intent-final.json`。

### 2. 新镜像 PostgreSQL / Redis / Celery 链路：通过

新镜像重建后（`supportflow-api`、`supportflow-worker`）执行 `scripts.e2e_real_infra`：

```json
{
  "auth_rbac": "passed",
  "redis_fixed_window": [true, true, false],
  "session_id": 20,
  "celery_terminal_status": "final",
  "trace_id": "e2e-6ebb95ed86234c69",
  "worker_trace": "passed"
}
```

覆盖：登录与 RBAC（普通用户访问管理端 403、管理员 200）、Redis 真实固定窗口
INCR 限流、API 入队、Celery worker 消费至终态、trace_id 由 API 透传到 worker
（`--worker-log` 断言，非人工目视）。
后端为真实 PostgreSQL（`postgresql+psycopg://…@db:5432`），broker 为真实 Redis
（`redis://redis:6379/1`），Alembic 已升级至 head `20260730_0001`。

说明：该次 e2e 的 LLM 段使用 `LLM_PROVIDER=stub`。首次以真实 qwen 运行时，
worker 已成功连到 `dashscope.aliyuncs.com` 并收到 HTTP 403
`AllocationQuota.FreeTierOnly`（免费额度耗尽，由当天 140 次评测调用耗尽），
即 worker 侧的真实 LLM 网络与配置链路本身已验证可达，仅计费额度阻断了完成。
真实模型的对话正确性由上文 77 轮 Qwen 评测覆盖，因此改用 stub 隔离验证基础设施段。

## 遗留观察（本次验收暴露，非本次范围）

真实 LLM 返回 403 额度耗尽时，链路表现为硬失败而非优雅降级：

- `app/llm/circuit_breaker.py:157` 的 `except Exception` 把永久性错误
  （403 计费/鉴权）与瞬时故障（超时、5xx）同等计入熔断失败数。
- 未配置 `llm_fallback_model` 时 `_degrade` 直接抛 `CircuitOpenError`。
- `app/workers/agent_tasks.py:29` 随后重试（`max_retries=2`），对必然失败的
  错误共尝试 3 次。
- 结果：会话落到 `task_status=failed`、`latestReply=None`，用户侧无任何回复，
  也没有转人工。

同文件对 `ContextWindowExceeded` 已有"非 provider 故障则 discard、不污染熔断"的
先例，永久性鉴权/计费错误可归入同一类。对客服系统而言，LLM 不可用更合理的表现
可能是转人工并给出可读提示，而非 `failed` + 空回复。是否调整属产品决策，未在本次改动。

- 一轮跨多个订单执行任务；检测到多个订单时要求拆分。
- 一轮执行多个退款、退货或其他写操作；检测到后要求只选择一个。
- 复杂条件依赖图、并行任务或多 Agent 协作。
- 在退款确认文本中同时处理新查询；必须先单独确认或取消。
- 未选择订单时跨轮保存整组多意图任务；第一阶段会要求先选择共同订单再重试。
