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

## 受外部条件阻塞

以下两项尚未在多意图提交之后完成，不能视为通过：

1. 扩展后59场景、77轮的 Qwen 真实模型评测。启动前运行环境提示本会话用量已达上限，
   最早可在 2026-08-08 15:05 后重试；命令没有启动，也没有生成报告。
2. 使用新镜像重建 Docker 后的 PostgreSQL、Redis、Celery 关键链路。该操作同样需要当前
   受限的外部执行权限，因此没有用旧容器结果冒充新代码验收。

恢复后执行：

```bash
python -m app.eval.run_scenario_eval \
  --real --confirm-paid-run \
  --output eval/results/qwen-plus-multi-intent-final.json

docker compose -f deploy/docker-compose.yml up -d --build api worker
docker compose -f deploy/docker-compose.yml exec -T api python -m scripts.seed_data
python -m scripts.e2e_real_infra
```

## 第一阶段明确不支持

- 一轮跨多个订单执行任务；检测到多个订单时要求拆分。
- 一轮执行多个退款、退货或其他写操作；检测到后要求只选择一个。
- 复杂条件依赖图、并行任务或多 Agent 协作。
- 在退款确认文本中同时处理新查询；必须先单独确认或取消。
- 未选择订单时跨轮保存整组多意图任务；第一阶段会要求先选择共同订单再重试。
