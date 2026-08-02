import { mockApi } from "./mock";
import { auth } from "../auth";
import type {
  AdminMetrics,
  ChatActionRequest,
  ChatSession,
  SendMessageRequest,
  SendMessageResponse,
  SessionDetail,
  SessionSummary,
  TicketDetail,
  TicketAction,
  TicketSummary,
  TokenResponse,
  UserOrder
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const FALLBACK_API_TOKEN = import.meta.env.VITE_API_TOKEN;
// mock 规则：显式 VITE_USE_MOCK=true 强制 mock；显式 =false 强制连真后端（即便 dev）；
// 都不设时，dev 默认 mock（零配置预览 UI）、生产默认连后端。显式设置优先于 dev 默认。
const _mockFlag = import.meta.env.VITE_USE_MOCK;
const USE_MOCK = _mockFlag === "true" || (_mockFlag == null && import.meta.env.DEV);
// 仅用于显式的演示容灾。真实联调/生产默认禁止接口失败后伪装成 mock 成功。
const ENABLE_MOCK_FALLBACK = import.meta.env.VITE_ENABLE_MOCK_FALLBACK === "true";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = auth.token() ?? FALLBACK_API_TOKEN;
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    if (response.status === 401 && auth.token()) auth.clear();
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

async function streamSse(
  path: string,
  onSession: (session: ChatSession) => void,
  signal: AbortSignal
): Promise<void> {
  const token = auth.token() ?? FALLBACK_API_TOKEN;
  const response = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal
  });
  if (!response.ok || !response.body) {
    if (response.status === 401 && auth.token()) auth.clear();
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (event === "done") return;
      if (event === "error") throw new Error(data.join("\n") || "SSE stream failed");
      if (data.length) onSession(JSON.parse(data.join("\n")) as ChatSession);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

async function withFallback<T>(realCall: () => Promise<T>, mockCall: () => Promise<T>): Promise<T> {
  if (USE_MOCK) return mockCall();
  if (!ENABLE_MOCK_FALLBACK) return realCall();
  try {
    return await realCall();
  } catch (error) {
    console.warn("[SupportFlow] API unavailable; explicit mock fallback is enabled.", error);
    return mockCall();
  }
}

export const api = {
  useMock: USE_MOCK,
  async login(username: string, password: string): Promise<TokenResponse> {
    return request<TokenResponse>("/auth/token", {
      method: "POST",
      body: JSON.stringify({ username, password })
    });
  },
  streamChatSession(
    id: string,
    onSession: (session: ChatSession) => void,
    signal: AbortSignal
  ): Promise<void> {
    return streamSse(`/chat/session/${id}/stream`, onSession, signal);
  },
  sendMessage(payload: SendMessageRequest): Promise<SendMessageResponse> {
    return withFallback(
      () => request<SendMessageResponse>("/chat/message", { method: "POST", body: JSON.stringify(payload) }),
      () => mockApi.sendMessage(payload)
    );
  },
  submitChatAction(payload: ChatActionRequest): Promise<SendMessageResponse> {
    return withFallback(
      () => request<SendMessageResponse>("/chat/action", {
        method: "POST",
        body: JSON.stringify(payload)
      }),
      () => mockApi.submitAction(payload)
    );
  },
  getChatSession(id: string): Promise<ChatSession> {
    return withFallback(() => request<ChatSession>(`/chat/session/${id}`), () => mockApi.getSession(id));
  },
  listOrders(): Promise<UserOrder[]> {
    return withFallback(() => request<UserOrder[]>("/orders"), () => mockApi.listOrders());
  },
  getOrder(id: string): Promise<UserOrder> {
    return withFallback(() => request<UserOrder>(`/orders/${id}`), () => mockApi.getOrder(id));
  },
  getMetrics(): Promise<AdminMetrics> {
    return withFallback(() => request<AdminMetrics>("/admin/metrics"), () => mockApi.getMetrics());
  },
  listTickets(): Promise<TicketSummary[]> {
    return withFallback(() => request<TicketSummary[]>("/admin/tickets"), () => mockApi.listTickets());
  },
  getTicket(id: string): Promise<TicketDetail> {
    return withFallback(() => request<TicketDetail>(`/admin/tickets/${id}`), () => mockApi.getTicket(id));
  },
  handleTicket(id: string, action: TicketAction): Promise<TicketDetail> {
    return request<TicketDetail>(`/admin/tickets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(action)
    });
  },
  listSessions(): Promise<SessionSummary[]> {
    return withFallback(() => request<SessionSummary[]>("/admin/sessions"), () => mockApi.listSessions());
  },
  getSessionDetail(id: string): Promise<SessionDetail> {
    return withFallback(() => request<SessionDetail>(`/admin/sessions/${id}`), () => mockApi.getSessionDetail(id));
  }
};
