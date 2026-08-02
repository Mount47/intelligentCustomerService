# 真实模型综合场景评测

## 文件

- `real_scenario_cases.json`：50 个场景、67 个对话轮次；运行时创建隔离订单数据。
- `run_scenario_eval.py`：执行多轮场景，检查流程、工具、回复和数据库副作用。

## 数据覆盖

| 分类 | 重点 |
|---|---|
| `semantic_understanding` | 隐式意图、口语、错别字、否定、多意图、情绪、超范围 |
| `multi_turn_workflow` | 补槽、确认、迟疑、取消、重放、超时、流程打断、上下文损坏 |
| `business_boundaries` | 500 元、7 天、48 小时边界，特殊商品、缺失或冲突数据 |
| `security_and_guardrails` | IDOR、提示注入、危险写操作、第三方收款、赔偿承诺 |
| `anomaly_and_recovery` | 恶意参数混入、取消订单、避免误转人工 |

`severity=critical` 表示资金、安全或越权相关用例。综合分数之外，报告必须单独展示
`critical_failures`；总分很高但关键用例失败，仍然不能通过发布门槛。

## 数据集校验

只检查 JSON 结构、订单引用和断言字段，不运行模型：

```bash
python -m app.eval.run_scenario_eval --validate-only
```

## 零费用试跑

默认使用 Stub。语义泛化类用例在 Stub 下失败是预期现象；它可以验证评测程序、确定性业务边界和
多轮安全协议是否正常：

```bash
python -m app.eval.run_scenario_eval \
  --output eval/results/scenario-stub.json

# 只跑一个分类或场景
python -m app.eval.run_scenario_eval --category business_boundaries
python -m app.eval.run_scenario_eval --case-filter workflow-refund-confirm
```

## 真实模型评测

配置 `.env` 后显式确认付费：

```bash
python -m app.eval.run_scenario_eval \
  --real --confirm-paid-run --repeat 3 \
  --output eval/results/qwen-plus-scenario-v1.json
```

建议先小范围试跑，确认模型支持 Function Calling：

```bash
python -m app.eval.run_scenario_eval \
  --real --confirm-paid-run \
  --category semantic_understanding \
  --output eval/results/semantic-smoke.json
```

程序退出码：全部断言通过为 `0`，存在失败为 `1`，配置或数据集错误为 `2`。

## 每轮断言

每个 `turn.expected` 支持：

- `intents` / `skills`：允许的意图或技能集合（用于确实存在多种合理解释的表达）；
- `skill` / `state` / `handoff`：单一预期路由与流程结果；
- `required_tools`：必须成功调用的所有工具；
- `any_tools`：至少成功调用一个；
- `forbidden_tools`：绝不能尝试调用；
- `all_tools_must_succeed` / `max_tool_calls`：工具质量与循环上限；
- `refund_count`：本场景订单上的退款申请总数；
- `deduplicated`：消息是否命中幂等；
- `reply_all` / `reply_any` / `reply_none`：事实包含和禁语断言。

多轮还支持：

- `attach_order`：模拟前端已经绑定订单；
- `{{order_no:alias}}`：在自然语言中填入运行时订单号，测试跨轮补槽；
- `client_message_key`：复用消息编号，模拟网络重试；
- `before.pending_action_age_seconds`：模拟确认过期；
- `before.corrupt_pending_context`：模拟持久化上下文损坏；
- `before.set_order_amount`：模拟确认期间订单金额变化。

## 如何读报告

必须同时看：

1. `critical_failures`：资金和安全用例应为 0；
2. 分类通过率：不能只看容易的规则用例拉高总分；
3. `model_calls/model_errors/model_latency_ms`：模型稳定性；
4. `actual.tools/successful_tools/failed_tools`：Function Calling 是否真的可靠；
5. `refund_count`：最终状态正确之外，数据库副作用也必须正确；
6. 三次重复结果：同一用例偶发失败仍需进入 bad-case 列表。

## 诚实边界

该程序使用临时 SQLite 并直接运行 Agent，适合语义、工具和业务流程评测，不证明 HTTP、PostgreSQL、
Redis、Celery 的联调效果，也不代表并发吞吐。完整基础设施应另跑
`scripts/e2e_real_infra.py` 和 Locust 压测。
