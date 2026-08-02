<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ChatDotRound, Goods, RefreshRight, Right } from "@element-plus/icons-vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import type { UserOrder } from "../api/types";

const router = useRouter();
const orders = ref<UserOrder[]>([]);
const loading = ref(true);
const error = ref("");
const statusFilter = ref("all");

const statusText: Record<string, string> = {
  pending_payment: "待付款",
  paid: "已付款",
  shipped: "运输中",
  delivered: "已签收",
  cancelled: "已取消"
};

const filteredOrders = computed(() => {
  if (statusFilter.value === "all") return orders.value;
  return orders.value.filter((order) => order.status === statusFilter.value);
});

function formatMoney(value: number) {
  return `¥${Number(value).toFixed(2)}`;
}

function formatDate(value?: string) {
  if (!value) return "时间待更新";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).format(new Date(value));
}

function productSummary(order: UserOrder) {
  if (!order.items.length) return "订单商品";
  const first = order.items[0];
  const extra = order.items.length > 1 ? ` 等 ${order.items.length} 件商品` : "";
  return `${first.productName}${extra}`;
}

async function loadOrders() {
  loading.value = true;
  error.value = "";
  try {
    orders.value = await api.listOrders();
  } catch {
    error.value = "订单暂时加载失败，请稍后重试。";
  } finally {
    loading.value = false;
  }
}

function startService(order: UserOrder) {
  void router.push({ path: "/chat", query: { orderId: String(order.id) } });
}

onMounted(loadOrders);
</script>

<template>
  <section class="workspace order-layout">
    <header class="page-head order-head">
      <div>
        <p class="eyebrow">我的售后</p>
        <h1>选择要咨询的订单</h1>
        <p class="page-intro">订单会自动关联到本次对话，您只需要说明遇到的问题。</p>
      </div>
      <el-button :icon="ChatDotRound" plain @click="router.push({ path: '/chat', query: { general: '1' } })">
        咨询其他问题
      </el-button>
    </header>

    <div class="order-toolbar">
      <el-segmented
        v-model="statusFilter"
        :options="[
          { label: '全部订单', value: 'all' },
          { label: '已付款', value: 'paid' },
          { label: '运输中', value: 'shipped' },
          { label: '已签收', value: 'delivered' }
        ]"
      />
      <span>共 {{ filteredOrders.length }} 个订单</span>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false">
      <el-button text :icon="RefreshRight" @click="loadOrders">重新加载</el-button>
    </el-alert>

    <div v-loading="loading" class="order-grid">
      <article v-for="order in filteredOrders" :key="order.id" class="order-card">
        <div class="order-card-top">
          <span class="order-date">{{ formatDate(order.createdAt ?? order.paidAt) }}</span>
          <span class="order-status" :class="order.status">{{ statusText[order.status] ?? order.status }}</span>
        </div>
        <div class="order-product">
          <div class="product-mark"><el-icon><Goods /></el-icon></div>
          <div>
            <h2>{{ productSummary(order) }}</h2>
            <p v-if="order.items.length">
              {{ order.items.map((item) => `${item.productName} ×${item.quantity}`).join("，") }}
            </p>
            <p class="order-number">订单号 {{ order.orderNo }}</p>
          </div>
        </div>
        <div class="order-card-bottom">
          <div>
            <small>实付款</small>
            <strong>{{ formatMoney(order.totalAmount) }}</strong>
          </div>
          <el-button type="primary" :icon="Right" @click="startService(order)">申请售后</el-button>
        </div>
      </article>

      <div v-if="!loading && !filteredOrders.length && !error" class="orders-empty">
        <el-icon><Goods /></el-icon>
        <strong>这里还没有符合条件的订单</strong>
        <span>可切换其他状态，或咨询不需要关联订单的问题。</span>
      </div>
    </div>
  </section>
</template>
