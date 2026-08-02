export type TaskStatus = "queued" | "processing" | "waiting_user_input" | "final" | "need_human" | "failed";
export type StepStatus = "pending" | "running" | "success" | "failed";

export interface TokenResponse {
  accessToken: string;
  tokenType: "bearer";
  expiresIn: number;
  userId: string;
  role: "user" | "admin";
}

export interface ChatMessage {
  id: string;
  sender: "user" | "agent" | "human" | "system";
  content: string;
  createdAt: string;
}

export interface AgentTimelineStep {
  id: string;
  kind: "intent" | "skill" | "tool" | "risk" | "reply" | "state";
  title: string;
  detail?: string;
  status: StepStatus;
  latencyMs?: number;
  createdAt?: string;
}

export interface AgentToolCall {
  id: string;
  sessionId: string;
  toolName: string;
  inputJson?: Record<string, unknown>;
  outputJson?: Record<string, unknown>;
  success: boolean;
  latencyMs?: number;
  errorMessage?: string;
  createdAt: string;
}

export interface ChatSession {
  id: string;
  ticketId?: string;
  taskStatus: TaskStatus;
  currentIntent?: string;
  currentSkill?: string;
  currentState?: string;
  finalStatus?: string;
  latestReply?: string;
  messages: ChatMessage[];
  steps: AgentTimelineStep[];
  toolCalls: AgentToolCall[];
  pendingAction?: PendingChatAction;
  tokenUsage: {
    modelName?: string;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    estimatedCost: number;
    cacheHit: boolean;
  };
}

export interface PendingChatAction {
  id: string;
  type: "refund_request" | "return_request";
  orderId: string;
  amount: number;
  createdAt?: string;
}

export interface SendMessageRequest {
  userId?: string;           // mock 兼容；真实后端身份来自 Bearer Token
  content: string;
  clientMessageId: string;
  ticketId?: string;        // 多轮对话：带同一工单 id 续接上下文（P1 对话记忆）
  orderId?: string;         // 首轮由用户从本人订单中选择，不要求用户手输订单号
}

export interface ChatActionRequest {
  ticketId: string;
  actionId: string;
  decision: "confirm" | "cancel";
  clientActionId: string;
}

export interface SendMessageResponse {
  sessionId: string;
  ticketId?: string;
  taskStatus: TaskStatus;
}

export interface UserOrderItem {
  id: string;
  productName: string;
  quantity: number;
  unitPrice: number;
}

export interface UserOrder {
  id: string;
  orderNo: string;
  status: string;
  totalAmount: number;
  productType: string;
  paidAt?: string;
  shippedAt?: string;
  deliveredAt?: string;
  createdAt?: string;
  items: UserOrderItem[];
}

export interface AdminMetrics {
  ticketCount: number;
  resolvedRate: number;
  handoffRate: number;
  avgTokenCost: number;
  p95LatencyMs: number;
  activeSessions: number;
  queueDepth?: number | null;
  // sla 是后端普通 dict，键不被 camel 化（met_rate 保持 snake）
  sla?: { total: number; met: number; breached: number; pending: number; met_rate: number };
  quality?: { count: number; resolution: number; tool: number; compliance: number };
}

export interface TicketSummary {
  id: string;
  userId: string;
  orderNo?: string;
  category: "refund" | "logistics" | "complaint" | "other";
  priority: "low" | "normal" | "high";
  status: string;
  currentState: string;
  updatedAt: string;
  slaDeadline?: string;
  assignedTo?: string;
}

export interface SessionSummary {
  id: string;
  ticketId?: string;
  currentIntent?: string;
  currentSkill?: string;
  taskStatus: TaskStatus;
  totalTokens: number;
  estimatedCost: number;
  totalLatencyMs?: number;
  updatedAt: string;
}

export interface TicketDetail extends TicketSummary {
  stateTimeline: AgentTimelineStep[];
  messages: ChatMessage[];
}

export interface TicketAction {
  action: "assign" | "resolve" | "reject" | "close";
  assignedTo?: string;
  note?: string;
}

export interface SessionDetail extends ChatSession {
  totalLatencyMs?: number;
}
