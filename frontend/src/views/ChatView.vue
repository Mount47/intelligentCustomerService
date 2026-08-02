<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  Back,
  ChatDotRound,
  CircleCheck,
  Clock,
  Goods,
  Plus,
  RefreshRight,
  Warning
} from "@element-plus/icons-vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api/client";
import type { AgentTimelineStep, ChatSession, TaskStatus, UserOrder } from "../api/types";

const route = useRoute();
const router = useRouter();
const selectedOrder = ref<UserOrder | null>(null);
const isGeneralQuestion = computed(() => route.query.general === "1");
const input = ref("");
const session = ref<ChatSession | null>(null);
const ticketId = ref<string | undefined>();
const loading = ref(false);
const orderLoading = ref(true);
const sending = ref(false);
const error = ref("");
const streaming = ref(false);
const actionDialogVisible = ref(false);
const actionSubmitting = ref(false);
const actionError = ref("");
const actionAttempt = ref<{ decision: "confirm" | "cancel"; id: string } | null>(null);
let pollTimer: number | undefined;
let streamAbort: AbortController | undefined;

const statusMeta: Record<TaskStatus, {
  text: string;
  type: "info" | "warning" | "success" | "danger";
}> = {
  queued: { text: "已收到，等待处理", type: "info" },
  processing: { text: "正在处理", type: "warning" },
  waiting_user_input: { text: "等待您的回复", type: "warning" },
  final: { text: "本轮处理完成", type: "success" },
  need_human: { text: "已转交人工客服", type: "danger" },
  failed: { text: "处理遇到问题", type: "danger" }
};

const quickQuestions = computed(() => {
  if (isGeneralQuestion.value) return ["想了解售后规则", "我要转人工", "如何申请电子发票"];
  if (selectedOrder.value?.status === "shipped") {
    return ["我的快递到哪里了？", "物流很久没有更新", "我想申请退款"];
  }
  if (selectedOrder.value?.status === "delivered") {
    return ["我想申请退货", "商品有质量问题", "物流显示签收但没有收到"];
  }
  return ["我想申请退款", "什么时候可以发货？", "我要修改收货信息"];
});

const isPolling = computed(() =>
  session.value?.taskStatus === "queued" || session.value?.taskStatus === "processing"
);
const pendingAction = computed(() => session.value?.pendingAction);
const pendingOperation = computed(() =>
  pendingAction.value?.type === "return_request" ? "退货" : "退款"
);
const pendingActionLabel = computed(() => `确认申请${pendingOperation.value}`);

function formatMoney(value: number) {
  return `¥${Number(value).toFixed(2)}`;
}

function productSummary(order: UserOrder) {
  if (!order.items.length) return "订单商品";
  return order.items.length > 1
    ? `${order.items[0].productName} 等 ${order.items.length} 件商品`
    : order.items[0].productName;
}

function senderName(sender: string) {
  if (sender === "user") return "我";
  if (sender === "human") return "人工客服";
  if (sender === "system") return "系统消息";
  return "智能客服";
}

function friendlyStepTitle(step: AgentTimelineStep) {
  const titles: Record<AgentTimelineStep["kind"], string> = {
    intent: "理解您的问题",
    skill: "确定处理方式",
    tool: "核对相关信息",
    risk: "检查处理条件",
    reply: "整理处理结果",
    state: "更新处理进度"
  };
  return titles[step.kind] ?? "正在处理";
}

function friendlyStepDetail(step: AgentTimelineStep) {
  if (step.status === "failed") return "这一步未能完成，系统会安排后续处理。";
  if (step.status === "running") return "正在处理，请稍候。";
  if (step.status === "success") return "已完成";
  return "等待处理";
}

function stepIcon(step: AgentTimelineStep) {
  if (step.status === "failed") return Warning;
  if (step.status === "success") return CircleCheck;
  if (step.status === "running") return RefreshRight;
  return Clock;
}

async function loadOrder() {
  const orderId = typeof route.query.orderId === "string" ? route.query.orderId : "";
  if (!orderId) {
    orderLoading.value = false;
    if (!isGeneralQuestion.value) await router.replace("/orders");
    return;
  }
  try {
    selectedOrder.value = await api.getOrder(orderId);
  } catch {
    error.value = "该订单不存在或不属于当前账号，请重新选择订单。";
  } finally {
    orderLoading.value = false;
  }
}

function newConversation() {
  stopStream();
  ticketId.value = undefined;
  session.value = null;
  input.value = "";
  error.value = "";
  actionDialogVisible.value = false;
  actionError.value = "";
  actionAttempt.value = null;
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
  } catch {
    error.value = "处理进度暂时无法更新，请稍后重试。";
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

function startStream(id: string) {
  stopStream();
  if (api.useMock || typeof ReadableStream === "undefined") {
    void refreshSession(id);
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
  if (
    !content || sending.value || pendingAction.value
    || (!selectedOrder.value && !isGeneralQuestion.value)
  ) return;
  sending.value = true;
  error.value = "";
  stopPoll();
  try {
    const response = await api.sendMessage({
      content,
      clientMessageId: `web-${Date.now()}`,
      ticketId: ticketId.value,
      orderId: selectedOrder.value ? String(selectedOrder.value.id) : undefined
    });
    ticketId.value = response.ticketId ?? ticketId.value;
    input.value = "";
    startStream(response.sessionId);
  } catch (err) {
    const detail = err instanceof Error ? err.message : "";
    error.value = detail.includes("resource_access_denied")
      ? "当前对话关联的订单已改变，请新建对话后重试。"
      : "消息发送失败，请稍后重试。";
  } finally {
    sending.value = false;
  }
}

function openActionConfirmation() {
  actionError.value = "";
  actionDialogVisible.value = true;
}

function getActionAttempt(decision: "confirm" | "cancel") {
  if (actionAttempt.value?.decision === decision) return actionAttempt.value.id;
  const randomPart = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  const id = `web-action-${randomPart}`;
  actionAttempt.value = { decision, id };
  return id;
}

async function submitPendingAction(decision: "confirm" | "cancel") {
  const action = pendingAction.value;
  const activeTicketId = session.value?.ticketId ?? ticketId.value;
  if (!action || !activeTicketId || actionSubmitting.value) return;
  actionSubmitting.value = true;
  actionError.value = "";
  error.value = "";
  stopStream();
  try {
    const response = await api.submitChatAction({
      ticketId: String(activeTicketId),
      actionId: action.id,
      decision,
      clientActionId: getActionAttempt(decision)
    });
    ticketId.value = response.ticketId ?? String(activeTicketId);
    if (session.value) session.value.pendingAction = undefined;
    actionDialogVisible.value = false;
    actionAttempt.value = null;
    startStream(response.sessionId);
  } catch (err) {
    const detail = err instanceof Error ? err.message : "";
    actionError.value = detail.includes("pending_action_invalid")
      ? "确认内容已经变化或过期，请刷新页面后重新发起申请。"
      : "操作提交失败，请稍后重试。";
  } finally {
    actionSubmitting.value = false;
  }
}

onMounted(loadOrder);
onBeforeUnmount(stopStream);
</script>

<template>
  <section class="workspace chat-layout" v-loading="orderLoading">
    <header class="page-head chat-head">
      <div>
        <button class="back-link" type="button" @click="router.push('/orders')">
          <el-icon><Back /></el-icon>
          返回我的订单
        </button>
        <p class="eyebrow">{{ isGeneralQuestion ? "其他问题咨询" : "订单售后" }}</p>
        <h1>{{ selectedOrder ? productSummary(selectedOrder) : "售后对话" }}</h1>
      </div>
      <div class="chat-head-actions">
        <el-tag v-if="session" :type="statusMeta[session.taskStatus].type" effect="light">
          {{ statusMeta[session.taskStatus].text }}
        </el-tag>
        <el-button :icon="Plus" plain @click="newConversation">新建对话</el-button>
      </div>
    </header>

    <div v-if="selectedOrder" class="selected-order">
      <div class="product-mark"><el-icon><Goods /></el-icon></div>
      <div>
        <small>本次咨询订单</small>
        <strong>{{ selectedOrder.orderNo }}</strong>
      </div>
      <span>{{ selectedOrder.items.map((item) => `${item.productName} ×${item.quantity}`).join("，") || "订单商品" }}</span>
      <b>{{ formatMoney(selectedOrder.totalAmount) }}</b>
      <el-button text @click="router.push('/orders')">更换订单</el-button>
    </div>
    <div v-else-if="isGeneralQuestion" class="selected-order general-order">
      <div class="product-mark"><el-icon><ChatDotRound /></el-icon></div>
      <div>
        <small>本次咨询</small>
        <strong>不关联具体订单</strong>
      </div>
      <span>适合咨询售后规则、发票、优惠券或人工服务。</span>
      <el-button text @click="router.push('/orders')">选择订单</el-button>
    </div>

    <el-alert v-if="error && !selectedOrder && !isGeneralQuestion" :title="error" type="error" show-icon :closable="false">
      <el-button text @click="router.push('/orders')">重新选择订单</el-button>
    </el-alert>

    <section v-else class="chat-panel">
      <div class="conversation">
        <div class="message-list" v-loading="loading && !session">
          <div v-if="!session" class="empty-state">
            <el-icon><ChatDotRound /></el-icon>
            <strong>{{ selectedOrder ? "请告诉我这笔订单遇到了什么问题" : "请告诉我您想咨询什么" }}</strong>
            <span>订单信息已经准备好，不用再输入订单号。</span>
            <div class="quick-questions">
              <button v-for="question in quickQuestions" :key="question" type="button" @click="input = question">
                {{ question }}
              </button>
            </div>
          </div>
          <article
            v-for="message in session?.messages ?? []"
            :key="message.id"
            class="bubble-row"
            :class="message.sender"
          >
            <div class="bubble">
              <small>{{ senderName(message.sender) }}</small>
              <p>{{ message.content }}</p>
            </div>
          </article>

          <section v-if="pendingAction" class="pending-action-card" aria-label="待确认操作">
            <div class="pending-action-copy">
              <span>需要您确认</span>
              <strong>{{ pendingActionLabel }}</strong>
              <p>
                本次{{ pendingOperation }}金额为
                <b>{{ formatMoney(pendingAction.amount) }}</b>。确认前您仍可取消，不会创建申请。
              </p>
            </div>
            <div class="pending-action-buttons">
              <el-button
                :disabled="actionSubmitting"
                @click="submitPendingAction('cancel')"
              >
                取消申请
              </el-button>
              <el-button
                type="primary"
                :disabled="actionSubmitting"
                @click="openActionConfirmation"
              >
                {{ pendingActionLabel }}
              </el-button>
            </div>
            <p v-if="actionError" class="action-error">{{ actionError }}</p>
          </section>
        </div>

        <form class="composer" @submit.prevent="submitMessage">
          <el-input
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            resize="none"
            :disabled="Boolean(pendingAction)"
            :placeholder="pendingAction ? '请先确认或取消当前申请' : '请描述您遇到的问题…'"
            @keydown.enter.exact.prevent="submitMessage"
          />
          <el-button
            type="primary"
            native-type="submit"
            :loading="sending"
            :disabled="!input.trim() || Boolean(pendingAction)"
          >
            发送
          </el-button>
          <small>{{ pendingAction ? "请先处理上方待确认申请" : "按 Enter 发送，Shift + Enter 换行" }}</small>
        </form>
        <el-alert v-if="error && (selectedOrder || isGeneralQuestion)" :title="error" type="error" show-icon :closable="false" />
      </div>

      <aside class="trace-panel">
        <div class="panel-title">
          <span>处理进度</span>
          <span v-if="streaming || isPolling" class="live-status">
            <i></i>实时更新
          </span>
        </div>
        <p class="progress-copy">系统会自动核对订单和售后条件，需要人工时会直接转交。</p>

        <div v-if="!session" class="progress-waiting">
          <ol>
            <li><span>1</span>理解您的问题</li>
            <li><span>2</span>核对订单信息</li>
            <li><span>3</span>给出处理结果</li>
          </ol>
        </div>

        <el-timeline v-else>
          <el-timeline-item
            v-for="step in session.steps"
            :key="step.id"
            :type="step.status === 'success' ? 'success' : step.status === 'failed' ? 'danger' : step.status === 'running' ? 'warning' : 'info'"
          >
            <div class="step-card" :class="step.status">
              <div>
                <el-icon :class="{ spin: step.status === 'running' }">
                  <component :is="stepIcon(step)" />
                </el-icon>
                <strong>{{ friendlyStepTitle(step) }}</strong>
              </div>
              <p>{{ friendlyStepDetail(step) }}</p>
            </div>
          </el-timeline-item>
        </el-timeline>

        <div v-if="session?.taskStatus === 'need_human'" class="human-note">
          <strong>已为您转交人工客服</strong>
          <span>订单和对话记录会一并提交，无需重复说明。</span>
        </div>
      </aside>
    </section>

    <el-dialog
      v-model="actionDialogVisible"
      class="action-confirm-dialog"
      width="min(480px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :show-close="!actionSubmitting"
      @closed="actionError = ''"
    >
      <template #header>
        <div class="confirm-dialog-head">
          <span>最终确认</span>
          <strong>确认提交{{ pendingOperation }}申请？</strong>
        </div>
      </template>

      <div v-if="pendingAction" class="confirm-summary">
        <div>
          <span>操作</span>
          <strong>申请{{ pendingOperation }}</strong>
        </div>
        <div>
          <span>订单</span>
          <strong>{{ selectedOrder?.orderNo ?? `#${pendingAction.orderId}` }}</strong>
        </div>
        <div>
          <span>商品</span>
          <strong>{{ selectedOrder ? productSummary(selectedOrder) : "当前订单商品" }}</strong>
        </div>
        <div class="amount-row">
          <span>申请金额</span>
          <strong>{{ formatMoney(pendingAction.amount) }}</strong>
        </div>
      </div>
      <p class="confirm-warning">
        点击“确认提交”后，系统才会正式创建{{ pendingOperation }}申请。请核对以上信息。
      </p>
      <p v-if="actionError" class="action-error">{{ actionError }}</p>

      <template #footer>
        <el-button :disabled="actionSubmitting" @click="actionDialogVisible = false">
          返回检查
        </el-button>
        <el-button
          type="danger"
          :loading="actionSubmitting"
          @click="submitPendingAction('confirm')"
        >
          确认提交
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>
