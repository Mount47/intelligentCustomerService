<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CircleCheck, Clock, Cpu, Message, Plus, RefreshRight, Warning } from "@element-plus/icons-vue";
import { api } from "../api/client";
import type { AgentTimelineStep, ChatSession, TaskStatus } from "../api/types";

// 仅 mock 模式切换演示账号；真实接口身份来自 VITE_API_TOKEN。
const demoAccounts = [
  { id: "11", label: "demo（场景订单专用，见 seed_data 对照表）" },
  { id: "13", label: "bulk_user001" },
  { id: "14", label: "bulk_user002" },
  { id: "15", label: "bulk_user003（VIP）" },
  { id: "16", label: "bulk_user004" },
  { id: "18", label: "bulk_user006（VIP）" }
];
const userId = ref(demoAccounts[0].id);
const input = ref("我要退款 订单 DEMO-REFUND-LOW");
const session = ref<ChatSession | null>(null);
const ticketId = ref<string | undefined>(undefined);   // 同一对话续接的工单 id（P1 对话记忆）
const loading = ref(false);
const sending = ref(false);
const error = ref("");
const streaming = ref(false);   // 是否正经由 SSE 实时接收
let pollTimer: number | undefined;
let streamAbort: AbortController | undefined;

// 新对话：丢弃当前工单与会话，下一条消息会新建工单
function newConversation() {
  stopStream();
  ticketId.value = undefined;
  session.value = null;
  error.value = "";
}
// 切换用户时重置对话（工单归属某用户，避免跨用户复用）
watch(userId, newConversation);

const statusMeta: Record<TaskStatus, { label: string; type: "info" | "warning" | "success" | "danger"; text: string }> = {
  queued: { label: "queued", type: "info", text: "排队中" },
  processing: { label: "processing", type: "warning", text: "处理中" },
  waiting_user_input: { label: "waiting_user_input", type: "warning", text: "等待您的回复" },
  final: { label: "final", type: "success", text: "已解决" },
  need_human: { label: "need_human", type: "danger", text: "转人工" },
  failed: { label: "failed", type: "danger", text: "失败" }
};

const isPolling = computed(() => session.value?.taskStatus === "queued" || session.value?.taskStatus === "processing");
const tokenCost = computed(() => session.value ? `$${session.value.tokenUsage.estimatedCost.toFixed(4)}` : "$0.0000");

function stepIcon(step: AgentTimelineStep) {
  if (step.status === "failed") return Warning;
  if (step.status === "success") return CircleCheck;
  if (step.status === "running") return RefreshRight;
  return Clock;
}

async function refreshSession(id: string) {
  loading.value = true;
  try {
    session.value = await api.getChatSession(id);
    if (session.value.taskStatus === "queued" || session.value.taskStatus === "processing") {
      schedulePoll(id);
    } else {
      stopPoll();
    }
  } finally {
    loading.value = false;
  }
}

function schedulePoll(id: string) {
  stopPoll();
  pollTimer = window.setTimeout(() => refreshSession(id), 800);
}

function stopPoll() {
  if (pollTimer) window.clearTimeout(pollTimer);
  pollTimer = undefined;
}

// 使用 fetch ReadableStream 消费 SSE，因此可携带 Authorization: Bearer。
function startStream(id: string) {
  stopStream();
  if (api.useMock || typeof ReadableStream === "undefined") {
    void refreshSession(id);   // 退回轮询
    return;
  }
  loading.value = true;
  let gotData = false;
  const controller = new AbortController();
  streamAbort = controller;
  streaming.value = true;
  void api.streamChatSession(
    id,
    (next) => {
      gotData = true;
      loading.value = false;
      session.value = next;
    },
    controller.signal
  ).catch((err) => {
    if (err instanceof DOMException && err.name === "AbortError") return;
    if (!gotData) void refreshSession(id);
  }).finally(() => {
    if (streamAbort === controller) {
      streamAbort = undefined;
      streaming.value = false;
    }
    loading.value = false;
  });
}

function stopStream() {
  streamAbort?.abort();
  streamAbort = undefined;
  streaming.value = false;
  stopPoll();
}

async function submitMessage() {
  const content = input.value.trim();
  if (!content || sending.value) return;
  sending.value = true;
  error.value = "";
  stopPoll();
  try {
    const response = await api.sendMessage({
      ...(api.useMock ? { userId: userId.value } : {}),
      content,
      clientMessageId: `web-${Date.now()}`,
      ticketId: ticketId.value          // 第二条起带上工单 id，后端续接上下文
    });
    ticketId.value = response.ticketId ?? ticketId.value;   // 记住工单，供下一轮续接
    input.value = "";
    startStream(response.sessionId);        // SSE 实时接收（失败自动退回轮询）
  } catch (err) {
    error.value = err instanceof Error ? err.message : "发送失败";
  } finally {
    sending.value = false;
  }
}

onBeforeUnmount(stopStream);
</script>

<template>
  <section class="workspace chat-layout">
    <header class="page-head">
      <div>
        <p class="eyebrow">用户端 /chat · Bearer 身份认证</p>
        <h1>售后对话</h1>
      </div>
      <el-button :icon="Plus" plain size="small" @click="newConversation">新对话</el-button>
      <div class="status-strip" v-if="session">
        <el-tag :type="statusMeta[session.taskStatus].type" effect="dark">
          {{ statusMeta[session.taskStatus].text }}
        </el-tag>
        <span>意图：{{ session.currentIntent ?? "待识别" }}</span>
        <span>技能：{{ session.currentSkill ?? "待路由" }}</span>
        <span>Token：{{ session.tokenUsage.totalTokens }}</span>
        <span>成本：{{ tokenCost }}</span>
      </div>
    </header>

    <section class="chat-panel">
      <div class="conversation">
        <div class="message-list" v-loading="loading && !session">
          <div v-if="!session" class="empty-state">
            <el-icon><Message /></el-icon>
            <strong>输入一个售后问题开始演示</strong>
            <span>前端通过 SSE 实时接收处理进度，逐步点亮 Agent 决策轨迹（异常自动退回轮询）。</span>
          </div>
          <article
            v-for="message in session?.messages ?? []"
            :key="message.id"
            class="bubble-row"
            :class="message.sender"
          >
            <div class="bubble">
              <small>{{ message.sender === "user" ? "用户" : "SupportFlow Agent" }}</small>
              <p>{{ message.content }}</p>
            </div>
          </article>
        </div>

        <form class="composer" @submit.prevent="submitMessage">
          <el-select v-if="api.useMock" v-model="userId" class="user-input" aria-label="模拟登录账号">
            <el-option v-for="acc in demoAccounts" :key="acc.id" :label="acc.label" :value="acc.id" />
          </el-select>
          <el-input
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            resize="none"
            placeholder="输入售后问题；涉及订单可带订单号，例如：我要退款 订单 DEMO-REFUND-LOW"
          />
          <el-button type="primary" native-type="submit" :loading="sending">
            发送
          </el-button>
        </form>
        <el-alert v-if="error" :title="error" type="error" show-icon />
      </div>

      <aside class="trace-panel">
        <div class="panel-title">
          <el-icon><Cpu /></el-icon>
          <span>Agent 执行轨迹</span>
          <el-tag v-if="streaming" size="small" type="success">实时流式</el-tag>
          <el-tag v-else-if="isPolling" size="small" type="warning">轮询中</el-tag>
        </div>
        <el-timeline>
          <el-timeline-item
            v-for="step in session?.steps ?? []"
            :key="step.id"
            :type="step.status === 'success' ? 'success' : step.status === 'failed' ? 'danger' : step.status === 'running' ? 'warning' : 'info'"
          >
            <div class="step-card" :class="step.status">
              <div>
                <el-icon :class="{ spin: step.status === 'running' }">
                  <component :is="stepIcon(step)" />
                </el-icon>
                <strong>{{ step.title }}</strong>
              </div>
              <p>{{ step.detail ?? "等待后端状态更新" }}</p>
              <small v-if="step.latencyMs">{{ step.latencyMs }}ms</small>
            </div>
          </el-timeline-item>
        </el-timeline>
        <div class="audit-preview" v-if="session?.toolCalls.length">
          <strong>工具调用审计</strong>
          <div v-for="call in session.toolCalls" :key="call.id">
            <span>{{ call.toolName }}</span>
            <el-tag size="small" :type="call.success ? 'success' : 'danger'">
              {{ call.success ? "success" : "failed" }}
            </el-tag>
          </div>
        </div>
      </aside>
    </section>
  </section>
</template>
