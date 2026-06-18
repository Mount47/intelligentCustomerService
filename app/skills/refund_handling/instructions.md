# RefundHandlingSkill 指令

触发意图：refund_request / return_request

LLM 在 loop 中**只读**取信息（get_order_detail / check_refund_policy / search_policy_docs），
帮助理解与解释；**最终的退款决策与写操作由 Skill.finalize 确定性执行**，不交给 LLM：

1. 无 order_id → 先向用户索取订单号（plan.required_info 短路）。
2. 核对订单归属与退款政策（check_refund_policy）。
3. 不可退（订单不存在/非本人/已取消）→ 转人工，不编造。
4. 高风险（金额 > 阈值 / 生鲜·定制 / 已签收超售后期）→ **不自动退款**，创建退款草稿(pending_human) + 升级工单 → 转人工。
5. 低风险且可退 → 创建退款草稿(draft)，回复并引用政策来源。

禁止：承诺一定退款/赔偿；绕过订单校验；退款到非本人账户。以上由 guardrails + 服务层共同兜底。
