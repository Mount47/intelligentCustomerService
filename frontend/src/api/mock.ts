import type {
  AdminMetrics,
  ChatActionRequest,
  ChatMessage,
  ChatSession,
  SendMessageRequest,
  SendMessageResponse,
  SessionDetail,
  SessionSummary,
  TicketDetail,
  TicketSummary,
  UserOrder
} from "./types";

const sessions = new Map<string, {
  createdAt: number;
  content: string;
  userId: string;
  actionDecision?: "confirm" | "cancel";
}>();

const demoOrders: UserOrder[] = [
  {
    id: "101",
    orderNo: "DEMO-REFUND-LOW",
    status: "paid",
    totalAmount: 120,
    productType: "normal",
    paidAt: "2026-07-26T09:30:00Z",
    createdAt: "2026-07-26T09:28:00Z",
    items: [{ id: "1001", productName: "蓝牙降噪耳机", quantity: 1, unitPrice: 120 }]
  },
  {
    id: "102",
    orderNo: "DEMO-LOGI-NORMAL",
    status: "shipped",
    totalAmount: 238.9,
    productType: "normal",
    paidAt: "2026-07-24T12:15:00Z",
    shippedAt: "2026-07-25T08:20:00Z",
    createdAt: "2026-07-24T12:12:00Z",
    items: [{ id: "1002", productName: "机械键盘", quantity: 1, unitPrice: 238.9 }]
  },
  {
    id: "103",
    orderNo: "DEMO-RETURN",
    status: "delivered",
    totalAmount: 329,
    productType: "normal",
    paidAt: "2026-07-18T16:40:00Z",
    deliveredAt: "2026-07-22T10:12:00Z",
    createdAt: "2026-07-18T16:38:00Z",
    items: [{ id: "1003", productName: "轻量运动鞋", quantity: 1, unitPrice: 329 }]
  }
];

const nowIso = () => new Date().toISOString();

function elapsedStage(createdAt: number): number {
  return Math.min(5, Math.floor((Date.now() - createdAt) / 850));
}

function buildSession(id: string): ChatSession {
  const item = sessions.get(id) ?? { createdAt: Date.now() - 5000, content: "我想申请订单 SO202606180018 的退款", userId: "1" };
  const stage = elapsedStage(item.createdAt);
  const isAction = Boolean(item.actionDecision);
  if (isAction) {
    const completed = stage >= 2;
    const reply = item.actionDecision === "confirm"
      ? "已为您创建退款申请（金额 120 元，单号 #R-1001），我们将尽快处理。"
      : "已取消本次退款申请，未提交。";
    return {
      id,
      ticketId: "T-20260618-0018",
      taskStatus: completed ? "final" : stage === 0 ? "queued" : "processing",
      currentIntent: item.actionDecision === "confirm" ? "refund_confirmation" : "cancel_refund",
      currentSkill: "refund_handling",
      currentState: completed ? "resolved_by_agent" : "processing",
      finalStatus: completed ? "resolved_by_agent" : undefined,
      latestReply: completed ? reply : undefined,
      messages: [
        {
          id: `${id}-user`,
          sender: "user",
          content: item.content,
          createdAt: new Date(item.createdAt).toISOString()
        },
        ...(completed ? [{
          id: `${id}-agent`,
          sender: "agent" as const,
          content: reply,
          createdAt: nowIso()
        }] : [])
      ],
      steps: [],
      toolCalls: [],
      tokenUsage: {
        promptTokens: 0,
        completionTokens: 0,
        totalTokens: 0,
        estimatedCost: 0,
        cacheHit: true
      }
    };
  }
  const status = stage >= 5 ? "waiting_user_input" : stage === 0 ? "queued" : "processing";
  const userMessage: ChatMessage = {
    id: `${id}-user`,
    sender: "user",
    content: item.content,
    createdAt: new Date(item.createdAt).toISOString()
  };
  const agentMessage: ChatMessage = {
    id: `${id}-agent`,
    sender: "agent",
    content: "已核对订单与退款政策：该订单符合低风险退款条件。请回复『确认』继续，或『取消』放弃，本次尚未创建退款申请。",
    createdAt: nowIso()
  };

  return {
    id,
    ticketId: "T-20260618-0018",
    taskStatus: status,
    currentIntent: stage >= 1 ? "refund_request" : undefined,
    currentSkill: stage >= 2 ? "RefundHandlingSkill" : undefined,
    currentState: stage >= 4 ? "waiting_user_confirm" : stage >= 1 ? "processing" : "queued",
    finalStatus: stage >= 5 ? "waiting_user_confirm" : undefined,
    latestReply: stage >= 5 ? agentMessage.content : undefined,
    messages: stage >= 5 ? [userMessage, agentMessage] : [userMessage],
    pendingAction: stage >= 5 ? {
      id: "mock-pending-action",
      type: "refund_request",
      orderId: "101",
      amount: 120,
      createdAt: nowIso()
    } : undefined,
    steps: [
      { id: "intent", kind: "intent", title: "识别意图", detail: "退款申请", status: stage >= 1 ? "success" : stage === 0 ? "running" : "pending" },
      { id: "skill", kind: "skill", title: "路由技能", detail: "RefundHandlingSkill", status: stage >= 2 ? "success" : stage === 1 ? "running" : "pending" },
      { id: "order", kind: "tool", title: "调用工具", detail: "查询订单 SO202606180018", status: stage >= 3 ? "success" : stage === 2 ? "running" : "pending", latencyMs: stage >= 3 ? 86 : undefined },
      { id: "policy", kind: "tool", title: "调用工具", detail: "核对退款政策：普通商品 / 未超 7 天 / 金额 < 500", status: stage >= 4 ? "success" : stage === 3 ? "running" : "pending", latencyMs: stage >= 4 ? 44 : undefined },
      { id: "risk", kind: "risk", title: "风险判断", detail: "低风险，进入用户二次确认", status: stage >= 4 ? "success" : "pending" },
      { id: "reply", kind: "reply", title: "生成回复", detail: "整理处理结果并写入消息记录", status: stage >= 5 ? "success" : stage === 4 ? "running" : "pending" }
    ],
    toolCalls: stage >= 3 ? [
      {
        id: "tool-1",
        sessionId: id,
        toolName: "order.lookup",
        inputJson: { order_no: "SO202606180018" },
        outputJson: { status: "delivered", amount: 238.9, product_type: "normal" },
        success: true,
        latencyMs: 86,
        createdAt: nowIso()
      },
      ...(stage >= 4 ? [{
        id: "tool-2",
        sessionId: id,
        toolName: "refund.policy_check",
        inputJson: { order_no: "SO202606180018", reason: "七天无理由" },
        outputJson: { eligible: true, risk_level: "low", require_human_approval: false },
        success: true,
        latencyMs: 44,
        createdAt: nowIso()
      }] : [])
    ] : [],
    tokenUsage: {
      modelName: "claude-opus-4-8",
      promptTokens: stage >= 5 ? 1320 : 0,
      completionTokens: stage >= 5 ? 184 : 0,
      totalTokens: stage >= 5 ? 1504 : 0,
      estimatedCost: stage >= 5 ? 0.0184 : 0,
      cacheHit: true
    }
  };
}

export const mockApi = {
  async listOrders(): Promise<UserOrder[]> {
    return demoOrders;
  },
  async getOrder(id: string): Promise<UserOrder> {
    const order = demoOrders.find((item) => String(item.id) === String(id));
    if (!order) throw new Error("订单不存在或不属于当前用户");
    return order;
  },
  async sendMessage(req: SendMessageRequest): Promise<SendMessageResponse> {
    const sessionId = `S-${Date.now()}`;
    sessions.set(sessionId, {
      createdAt: Date.now(), content: req.content, userId: req.userId ?? "11"
    });
    return { sessionId, ticketId: "T-20260618-0018", taskStatus: "queued" };
  },
  async submitAction(req: ChatActionRequest): Promise<SendMessageResponse> {
    const sessionId = `S-action-${Date.now()}`;
    sessions.set(sessionId, {
      createdAt: Date.now(),
      content: req.decision === "confirm" ? "确认提交退款申请" : "取消本次退款申请",
      userId: "11",
      actionDecision: req.decision
    });
    return { sessionId, ticketId: req.ticketId, taskStatus: "queued" };
  },
  async getSession(id: string): Promise<ChatSession> {
    return buildSession(id);
  },
  async getMetrics(): Promise<AdminMetrics> {
    return {
      ticketCount: 186,
      resolvedRate: 0.743,
      handoffRate: 0.118,
      avgTokenCost: 0.0216,
      p95LatencyMs: 1860,
      activeSessions: 9
    };
  },
  async listTickets(): Promise<TicketSummary[]> {
    return [
      { id: "T-20260618-0018", userId: "1", orderNo: "SO202606180018", category: "refund", priority: "normal", status: "resolved", currentState: "auto_refund_draft_created", updatedAt: nowIso(), slaDeadline: nowIso() },
      { id: "T-20260618-0042", userId: "7", orderNo: "SO202606180042", category: "logistics", priority: "high", status: "need_human", currentState: "handoff_pending", updatedAt: nowIso(), slaDeadline: nowIso() },
      { id: "T-20260618-0061", userId: "3", orderNo: "SO202606180061", category: "refund", priority: "normal", status: "processing", currentState: "policy_checking", updatedAt: nowIso(), slaDeadline: nowIso() }
    ];
  },
  async getTicket(id: string): Promise<TicketDetail> {
    const ticket = (await this.listTickets()).find((item) => item.id === id) ?? (await this.listTickets())[0];
    const session = buildSession("S-admin-preview");
    return { ...ticket, stateTimeline: session.steps, messages: session.messages };
  },
  async listSessions(): Promise<SessionSummary[]> {
    return [
      { id: "S-20260618-9001", ticketId: "T-20260618-0018", currentIntent: "refund_request", currentSkill: "RefundHandlingSkill", taskStatus: "final", totalTokens: 1504, estimatedCost: 0.0184, totalLatencyMs: 1240, updatedAt: nowIso() },
      { id: "S-20260618-9002", ticketId: "T-20260618-0042", currentIntent: "logistics_exception", currentSkill: "LogisticsSkill", taskStatus: "need_human", totalTokens: 1180, estimatedCost: 0.0142, totalLatencyMs: 1830, updatedAt: nowIso() }
    ];
  },
  async getSessionDetail(id: string): Promise<SessionDetail> {
    return { ...buildSession(id), totalLatencyMs: 1240 };
  }
};
