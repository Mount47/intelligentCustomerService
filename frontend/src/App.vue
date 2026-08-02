<script setup lang="ts">
import { DataAnalysis, Goods, SwitchButton } from "@element-plus/icons-vue";
import { useRouter } from "vue-router";
import { api } from "./api/client";
import { auth } from "./auth";

const router = useRouter();

async function logout() {
  auth.clear();
  await router.replace("/login");
}
</script>

<template>
  <div class="app-shell" :class="{ 'has-navigation': api.useMock || auth.isAuthenticated.value }">
    <aside v-if="api.useMock || auth.isAuthenticated.value" class="app-nav" aria-label="主导航">
      <div class="brand">
        <span class="brand-mark">SF</span>
        <div>
          <strong>SupportFlow</strong>
          <small>智能售后服务</small>
        </div>
      </div>
      <nav>
        <RouterLink to="/orders">
          <el-icon><Goods /></el-icon>
          我的订单
        </RouterLink>
        <RouterLink v-if="api.useMock || auth.isAdmin.value" to="/admin">
          <el-icon><DataAnalysis /></el-icon>
          运营管理
        </RouterLink>
      </nav>
      <div v-if="!api.useMock" class="nav-session">
        <span>{{ auth.usernameHint.value }}</span>
        <small>{{ auth.isAdmin.value ? "管理员" : "普通用户" }}</small>
        <el-button text :icon="SwitchButton" @click="logout">退出</el-button>
      </div>
    </aside>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>
