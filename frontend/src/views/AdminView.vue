<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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
const chartEl = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | undefined;

const metricCards = computed(() => {
  const m = metrics.value;
  return [
    { label: "工单量", value: m?.ticketCount ?? 0, icon: Tickets, suffix: "" },
    { label: "解决率", value: `${(((m?.resolvedRate ?? 0) * 100)).toFixed(1)}%`, icon: DocumentChecked, suffix: "" },
    { label: "转人工率", value: `${(((m?.handoffRate ?? 0) * 100)).toFixed(1)}%`, icon: Connection, suffix: "" },
    { label: "平均 token 成本", value: `$${(m?.avgTokenCost ?? 0).toFixed(4)}`, icon: Money, suffix: "" },
    { label: "P95 延迟", value: m?.p95LatencyMs ?? 0, icon: Stopwatch, suffix: "ms" },
    { label: "活跃会话", value: m?.activeSessions ?? 0, icon: TrendCharts, suffix: "" }
  ];
});

function drawChart() {
  if (!chartEl.value || !metrics.value) return;
  chart = chart ?? echarts.init(chartEl.value);
  chart.setOption({
    grid: { left: 32, right: 18, top: 24, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: ["09:00", "11:00", "13:00", "15:00", "17:00"], axisTick: { show: false } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "#e7edf3" } } },
    series: [
      { name: "自动解决", type: "line", smooth: true, data: [18, 26, 34, 39, 46], color: "#1f8a70", areaStyle: { color: "rgba(31, 138, 112, 0.12)" } },
      { name: "转人工", type: "line", smooth: true, data: [4, 6, 5, 8, 7], color: "#d97706" }
    ]
  });
}

async function loadDashboard() {
  loading.value = true;
  try {
    const [metricData, ticketData, sessionData] = await Promise.all([
      api.getMetrics(),
      api.listTickets(),
      api.listSessions()
    ]);
    metrics.value = metricData;
    tickets.value = ticketData;
    sessions.value = sessionData;
    activeTicket.value = ticketData[0] ? await api.getTicket(ticketData[0].id) : null;
    activeSession.value = sessionData[0] ? await api.getSessionDetail(sessionData[0].id) : null;
    requestAnimationFrame(drawChart);
  } finally {
    loading.value = false;
  }
}

async function selectTicket(row: TicketSummary) {
  activeTicket.value = await api.getTicket(row.id);
}

async function selectSession(row: SessionSummary) {
  activeSession.value = await api.getSessionDetail(row.id);
}

onMounted(loadDashboard);
</script>

<template>
  <section class="workspace admin-layout" v-loading="loading">
    <header class="page-head">
      <div>
        <p class="eyebrow">管理员端 /admin</p>
        <h1>运营与审计</h1>
      </div>
      <el-button @click="loadDashboard">刷新</el-button>
    </header>

    <section class="metric-grid">
      <article v-for="card in metricCards" :key="card.label" class="metric-card">
        <el-icon><component :is="card.icon" /></el-icon>
        <span>{{ card.label }}</span>
        <strong>{{ card.value }}<small>{{ card.suffix }}</small></strong>
      </article>
    </section>

    <section class="admin-main-grid">
      <div class="dashboard-panel">
        <div class="panel-title">解决趋势</div>
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
      </div>
    </section>

    <section class="admin-main-grid wide">
      <div class="dashboard-panel">
        <div class="panel-title">会话列表</div>
        <el-table :data="sessions" height="260" @row-click="selectSession">
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
        </template>
      </div>
    </section>
  </section>
</template>
