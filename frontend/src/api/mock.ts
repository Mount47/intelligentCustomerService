import type {
  AdminMetrics,
  ChatMessage,
  ChatSession,
  SendMessageRequest,
  SendMessageResponse,
  SessionDetail,
  SessionSummary,
  TicketDetail,
  TicketSummary
} from "./types";

const sessions = new Map<string, { createdAt: number; content: string; userId: string }>();

const nowIso = () => new Date().toISOString();

function elapsedStage(createdAt: number): number {
  return Math.min(5, Math.floor((Date.now() - createdAt) / 850));
}

function buildSession(id: string): ChatSession {
  const item = sessions.get(id) ?? { createdAt: Date.now() - 5000, content: "我想申请订单 SO202606180018 的退款", userId: "1" };
  const stage = elapsedStage(item.createdAt);
  const status = stage >= 5 ? "final" : stage === 0 ? "queued" : "processing";
  const userMessage: ChatMessage = {
    id: `${id}-user`,
    sender: "user",
    content: item.content,
    createdAt: new Date(item.createdAt).toISOString()
  };
  const agentMessage: ChatMessage = {
    id: `${id}-agent`,
    sender: "agent",
    content: "已核对订单与退款政策：该订单为普通商品，签收未超过 7 天，金额低于自动退款阈值。我已为你创建退款草稿，后续可在订单页确认提交。",
    createdAt: nowIso()
  };

  return {
    id,
    ticketId: "T-20260618-0018",
    taskStatus: status,
    currentIntent: stage >= 1 ? "refund_request" : undefined,
    currentSkill: stage >= 2 ? "RefundHandlingSkill" : undefined,
    currentState: stage >= 4 ? "auto_refund_draft_created" : stage >= 1 ? "processing" : "queued",
    finalStatus: stage >= 5 ? "resolved" : undefined,
    latestReply: stage >= 5 ? agentMessage.content : undefined,
    messages: stage >= 5 ? [userMessage, agentMessage] : [userMessage],
    steps: [
      { id: "intent", kind: "intent", title: "识别意图", detail: "退款申请", status: stage >= 1 ? "success" : stage === 0 ? "running" : "pending" },
      { id: "skill", kind: "skill", title: "路由技能", detail: "RefundHandlingSkill", status: stage >= 2 ? "success" : stage === 1 ? "running" : "pending" },
      { id: "order", kind: "tool", title: "调用工具", detail: "查询订单 SO202606180018", status: stage >= 3 ? "success" : stage === 2 ? "running" : "pending", latencyMs: stage >= 3 ? 86 : undefined },
      { id: "policy", kind: "tool", title: "调用工具", detail: "核对退款政策：普通商品 / 未超 7 天 / 金额 < 500", status: stage >= 4 ? "success" : stage === 3 ? "running" : "pending", latencyMs: stage >= 4 ? 44 : undefined },
      { id: "risk", kind: "risk", title: "风险判断", detail: "低风险，可自动创建退款草稿", status: stage >= 4 ? "success" : "pending" },
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
  async sendMessage(req: SendMessageRequest): Promise<SendMessageResponse> {
    const sessionId = `S-${Date.now()}`;
    sessions.set(sessionId, { createdAt: Date.now(), content: req.content, userId: req.userId });
    return { sessionId, ticketId: "T-20260618-0018", taskStatus: "queued" };
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
