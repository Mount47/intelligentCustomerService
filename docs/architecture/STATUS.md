# 项目状态

## 已实现

- 订单、物流、退款与人工升级主链路；
- 多轮补槽、退款确认、消息与退款幂等；
- FastAPI、PostgreSQL、Redis、Celery 与本地 thread 模式；
- 用户/管理员认证、RBAC 和资源归属校验；
- Session 精确消息绑定、worker 抢占、Ticket CAS；
- 前端登录、Bearer SSE、轮询降级和管理看板；
- 工具审计、token/成本、SLA、trace、离线评测和接入压测；
- 后端测试、前端构建和真实 Redis/Celery/PostgreSQL CI 验收。

## 交付前验收

- `pytest -q`
- `python -m scripts.demo_recruitment`
- `python -m app.eval.run_eval`
- `npm --prefix frontend run build`
- `docker compose -f deploy/docker-compose.yml config -q`
- 干净环境执行 Compose 启动、seed、登录、两轮退款与管理员处理闭环

## 明确不声称

- 不声称已接支付渠道完成真实退款；
- 不声称压测数据代表真实 LLM 推理吞吐；
- 不将执行轨迹称为模型思维链；
- 不将规则/stub 评测等同于真实模型语义泛化；
- 当前 HMAC Token 是项目级身份方案，不等同于企业 SSO/IAM。
