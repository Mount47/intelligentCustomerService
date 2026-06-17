export type TaskStatus = "queued" | "processing" | "final" | "need_human" | "failed";
export type StepStatus = "pending" | "running" | "success" | "failed";

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
  tokenUsage: {
    modelName?: string;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    estimatedCost: number;
    cacheHit: boolean;
  };
}

export interface SendMessageRequest {
  userId: string;
  content: string;
  clientMessageId: string;
}

export interface SendMessageResponse {
  sessionId: string;
  ticketId?: string;
  taskStatus: TaskStatus;
}

export interface AdminMetrics {
  ticketCount: number;
  resolvedRate: number;
  handoffRate: number;
  avgTokenCost: number;
  p95LatencyMs: number;
  activeSessions: number;
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

export interface SessionDetail extends ChatSession {
  totalLatencyMs?: number;
}
