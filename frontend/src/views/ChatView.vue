<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { CircleCheck, Clock, Cpu, Message, RefreshRight, Warning } from "@element-plus/icons-vue";
import { api } from "../api/client";
import type { AgentTimelineStep, ChatSession, TaskStatus } from "../api/types";

const userId = ref("11");   // seed 的 demo 用户；订单号见 seed_data 对照表
const input = ref("我要退款 订单 DEMO-REFUND-LOW");
const session = ref<ChatSession | null>(null);
const loading = ref(false);
const sending = ref(false);
const error = ref("");
let pollTimer: number | undefined;

const statusMeta: Record<TaskStatus, { label: string; type: "info" | "warning" | "success" | "danger"; text: string }> = {
  queued: { label: "queued", type: "info", text: "排队中" },
  processing: { label: "processing", type: "warning", text: "处理中" },
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

async function submitMessage() {
  const content = input.value.trim();
  if (!content || sending.value) return;
  sending.value = true;
  error.value = "";
  stopPoll();
  try {
    const response = await api.sendMessage({
      userId: userId.value,
      content,
      clientMessageId: `web-${Date.now()}`
    });
    await refreshSession(response.sessionId);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "发送失败";
  } finally {
    sending.value = false;
  }
}

onBeforeUnmount(stopPoll);
</script>

<template>
  <section class="workspace chat-layout">
    <header class="page-head">
      <div>
        <p class="eyebrow">用户端 /chat</p>
        <h1>售后对话</h1>
      </div>
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
            <span>前端会轮询 `GET /chat/session/{id}`，逐步点亮 Agent 决策轨迹。</span>
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
          <el-input v-model="userId" class="user-input" aria-label="用户 ID" />
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
          <span>思考过程</span>
          <el-tag v-if="isPolling" size="small" type="warning">轮询中</el-tag>
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
