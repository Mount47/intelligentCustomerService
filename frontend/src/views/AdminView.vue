<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Connection, DocumentChecked, Money, Stopwatch, Tickets, TrendCharts } from "@element-plus/icons-vue";
import * as echarts from "echarts";
import { api } from "../api/client";
import type { AdminMetrics, SessionDetail, SessionSummary, TicketDetail, TicketSummary } from "../api/types";

const metrics = ref<AdminMetrics | null>(null);
const tickets = ref<TicketSummary[]>([]);
const sessions = ref<SessionSummary[]>([]);
const activeTicket = ref<TicketDetail | null>(null);
const activeSession = ref<SessionDetail | null>(null);
const loading = ref(false);
const error = ref("");
const auto = ref(true);
const chartEl = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | undefined;
let timer: number | undefined;

// 真实时间序列：每次轮询累积一个点（活跃会话 / 队列深度）→ 动态削峰曲线（替代写死假数据）
const live = ref<{ t: string; active: number; queue: number }[]>([]);
const LIVE_CAP = 40;

const metricCards = computed(() => {
  const m = metrics.value;
  const slaRate = m?.sla ? `${(m.sla.met_rate * 100).toFixed(1)}%` : "—";
  return [
    { label: "工单量", value: m?.ticketCount ?? 0, icon: Tickets, suffix: "" },
    { label: "解决率", value: `${((m?.resolvedRate ?? 0) * 100).toFixed(1)}%`, icon: DocumentChecked, suffix: "" },
    { label: "转人工率", value: `${((m?.handoffRate ?? 0) * 100).toFixed(1)}%`, icon: Connection, suffix: "" },
    { label: "SLA 达成率", value: slaRate, icon: DocumentChecked, suffix: "" },
    { label: "平均 token 成本", value: `$${(m?.avgTokenCost ?? 0).toFixed(4)}`, icon: Money, suffix: "" },
    { label: "P95 延迟", value: m?.p95LatencyMs ?? 0, icon: Stopwatch, suffix: "ms" },
    { label: "活跃会话", value: m?.activeSessions ?? 0, icon: TrendCharts, suffix: "" }
  ];
});

function drawChart() {
  if (!chartEl.value) return;
  chart = chart ?? echarts.init(chartEl.value);
  chart.setOption({
    textStyle: { fontFamily: "JetBrains Mono, monospace" },
    grid: { left: 36, right: 18, top: 30, bottom: 28 },
    legend: { right: 0, top: 0, icon: "roundRect", textStyle: { color: "#5e6f69" } },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", boundaryGap: false, data: live.value.map((p) => p.t), axisTick: { show: false }, axisLine: { lineStyle: { color: "#e0e2d6" } } },
    yAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: "#e0e2d6", type: "dashed" } } },
    series: [
      { name: "活跃会话", type: "line", smooth: true, symbol: "circle", symbolSize: 6, lineStyle: { width: 3 }, data: live.value.map((p) => p.active), color: "#0f8a68", areaStyle: { color: "rgba(15, 138, 104, 0.14)" } },
      { name: "队列深度", type: "line", smooth: true, symbol: "circle", symbolSize: 6, lineStyle: { width: 3 }, data: live.value.map((p) => p.queue), color: "#c2592b", areaStyle: { color: "rgba(194, 89, 43, 0.10)" } }
    ]
  });
}

function pushLive(m: AdminMetrics) {
  const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  live.value.push({ t, active: m.activeSessions ?? 0, queue: m.queueDepth ?? 0 });
  if (live.value.length > LIVE_CAP) live.value.shift();
}

async function refreshMetrics() {
  try {
    const m = await api.getMetrics();
    metrics.value = m;
    pushLive(m);
    requestAnimationFrame(drawChart);
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "指标加载失败";
  }
}

async function loadDashboard() {
  loading.value = true;
  error.value = "";
  try {
    const [m, ticketData, sessionData] = await Promise.all([
      api.getMetrics(),
      api.listTickets(),
      api.listSessions()
    ]);
    metrics.value = m;
    pushLive(m);
    tickets.value = ticketData;
    sessions.value = sessionData;
    activeTicket.value = ticketData[0] ? await api.getTicket(ticketData[0].id) : null;
    activeSession.value = sessionData[0] ? await api.getSessionDetail(sessionData[0].id) : null;
    requestAnimationFrame(drawChart);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "看板加载失败";
  } finally {
    loading.value = false;
  }
}

function startAuto() {
  stopAuto();
  if (auto.value) timer = window.setInterval(refreshMetrics, 3000);
}
function stopAuto() {
  if (timer) window.clearInterval(timer);
  timer = undefined;
}
function toggleAuto() {
  auto.value = !auto.value;
  startAuto();
}

async function selectTicket(row: TicketSummary) {
  try {
    activeTicket.value = await api.getTicket(row.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "工单详情加载失败";
  }
}
async function selectSession(row: SessionSummary) {
  try {
    activeSession.value = await api.getSessionDetail(row.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "会话详情加载失败";
  }
}

onMounted(async () => {
  await loadDashboard();
  startAuto();
});
onBeforeUnmount(() => {
  stopAuto();
  chart?.dispose();
});
</script>

<template>
  <section class="workspace admin-layout" v-loading="loading">
    <header class="page-head">
      <div>
        <p class="eyebrow">管理员端 /admin</p>
        <h1>运营与审计</h1>
      </div>
      <el-button :type="auto ? 'success' : 'info'" plain size="small" @click="toggleAuto">
        {{ auto ? "自动刷新 · 开" : "自动刷新 · 关" }}
      </el-button>
      <el-button size="small" @click="loadDashboard">刷新</el-button>
    </header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <section class="metric-grid">
      <article v-for="card in metricCards" :key="card.label" class="metric-card">
        <el-icon><component :is="card.icon" /></el-icon>
        <span>{{ card.label }}</span>
        <strong>{{ card.value }}<small>{{ card.suffix }}</small></strong>
      </article>
    </section>

    <section class="admin-main-grid">
      <div class="dashboard-panel">
        <div class="panel-title">
          实时负载（每 3s）
          <el-tag size="small" :type="auto ? 'success' : 'info'">{{ auto ? "LIVE" : "暂停" }}</el-tag>
        </div>
        <div ref="chartEl" class="chart-box" />
      </div>
      <div class="dashboard-panel quality-panel">
        <div class="panel-title">质检评分</div>
        <p>QualityReviewSkill 后续接入。当前仅保留入口，不写入评分数据。</p>
      </div>
    </section>

    <section class="admin-main-grid wide">
      <div class="dashboard-panel">
        <div class="panel-title">工单列表</div>
        <el-table :data="tickets" height="300" @row-click="selectTicket">
          <template #empty><el-empty description="暂无工单" :image-size="60" /></template>
          <el-table-column prop="id" label="工单" width="150" />
          <el-table-column prop="category" label="类型" width="100" />
          <el-table-column prop="priority" label="优先级" width="90" />
          <el-table-column prop="status" label="状态" width="120" />
          <el-table-column prop="currentState" label="状态机" />
        </el-table>
      </div>
      <div class="dashboard-panel">
        <div class="panel-title">工单详情</div>
        <template v-if="activeTicket">
          <div class="detail-head">
            <strong>{{ activeTicket.id }}</strong>
            <el-tag>{{ activeTicket.status }}</el-tag>
          </div>
          <el-timeline>
            <el-timeline-item
              v-for="step in activeTicket.stateTimeline"
              :key="step.id"
              :type="step.status === 'success' ? 'success' : step.status === 'running' ? 'warning' : 'info'"
            >
              <strong>{{ step.title }}</strong>
              <p>{{ step.detail }}</p>
            </el-timeline-item>
          </el-timeline>
        </template>
        <el-empty v-else description="点击左侧工单查看处理时间线" :image-size="60" />
      </div>
    </section>

    <section class="admin-main-grid wide">
      <div class="dashboard-panel">
        <div class="panel-title">会话列表</div>
        <el-table :data="sessions" height="260" @row-click="selectSession">
          <template #empty><el-empty description="暂无会话" :image-size="60" /></template>
          <el-table-column prop="id" label="会话" width="170" />
          <el-table-column prop="currentIntent" label="意图" />
          <el-table-column prop="currentSkill" label="技能" />
          <el-table-column prop="taskStatus" label="任务状态" width="110" />
          <el-table-column prop="totalTokens" label="Token" width="100" />
          <el-table-column prop="estimatedCost" label="成本" width="90">
            <template #default="{ row }">${{ row.estimatedCost.toFixed(4) }}</template>
          </el-table-column>
        </el-table>
      </div>
      <div class="dashboard-panel">
        <div class="panel-title">工具调用审计</div>
        <template v-if="activeSession">
          <div class="cost-row">
            <span>{{ activeSession.tokenUsage.modelName }}</span>
            <strong>{{ activeSession.tokenUsage.totalTokens }} tokens / ${{ activeSession.tokenUsage.estimatedCost.toFixed(4) }}</strong>
          </div>
          <div class="tool-log" v-for="call in activeSession.toolCalls" :key="call.id">
            <div>
              <strong>{{ call.toolName }}</strong>
              <el-tag size="small" :type="call.success ? 'success' : 'danger'">{{ call.success ? "成功" : "失败" }}</el-tag>
            </div>
            <code>{{ JSON.stringify(call.inputJson) }}</code>
            <code>{{ JSON.stringify(call.outputJson) }}</code>
          </div>
          <el-empty v-if="!activeSession.toolCalls.length" description="该会话无工具调用" :image-size="60" />
        </template>
        <el-empty v-else description="点击左侧会话查看工具审计" :image-size="60" />
      </div>
    </section>
  </section>
</template>
