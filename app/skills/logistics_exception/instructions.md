# LogisticsExceptionSkill 指令

触发意图：logistics_query / logistics_exception

流程（finalize 据物流事实确定性决策）：
1. 无 order_id → 索取订单号。
2. 查物流（detect_exception）。无物流信息 → 转人工。
3. 显示已签收但用户反馈未收到 → 转人工核实（不承诺赔偿）。
4. 物流异常 / 超 48h 无更新 → 创建催件工单，告知用户已跟进。
5. 正常 → 告知当前状态与最新位置。

禁止：承诺具体送达时间（除非工具返回）；编造物流信息。
