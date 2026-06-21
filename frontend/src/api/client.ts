import { mockApi } from "./mock";
import type {
  AdminMetrics,
  ChatSession,
  SendMessageRequest,
  SendMessageResponse,
  SessionDetail,
  SessionSummary,
  TicketDetail,
  TicketSummary
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
// mock 规则：显式 VITE_USE_MOCK=true 强制 mock；显式 =false 强制连真后端（即便 dev）；
// 都不设时，dev 默认 mock（零配置预览 UI）、生产默认连后端。显式设置优先于 dev 默认。
const _mockFlag = import.meta.env.VITE_USE_MOCK;
const USE_MOCK = _mockFlag === "true" || (_mockFlag == null && import.meta.env.DEV);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

async function withFallback<T>(realCall: () => Promise<T>, mockCall: () => Promise<T>): Promise<T> {
  if (USE_MOCK) return mockCall();
  try {
    return await realCall();
  } catch (error) {
    console.warn("[SupportFlow] API unavailable, falling back to mock data.", error);
    return mockCall();
  }
}

export const api = {
  sendMessage(payload: SendMessageRequest): Promise<SendMessageResponse> {
    return withFallback(
      () => request<SendMessageResponse>("/chat/message", { method: "POST", body: JSON.stringify(payload) }),
      () => mockApi.sendMessage(payload)
    );
  },
  getChatSession(id: string): Promise<ChatSession> {
    return withFallback(() => request<ChatSession>(`/chat/session/${id}`), () => mockApi.getSession(id));
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
  listSessions(): Promise<SessionSummary[]> {
    return withFallback(() => request<SessionSummary[]>("/admin/sessions"), () => mockApi.listSessions());
  },
  getSessionDetail(id: string): Promise<SessionDetail> {
    return withFallback(() => request<SessionDetail>(`/admin/sessions/${id}`), () => mockApi.getSessionDetail(id));
  }
};
