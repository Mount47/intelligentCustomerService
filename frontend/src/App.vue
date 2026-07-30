<script setup lang="ts">
import { ChatRound, DataAnalysis, SwitchButton } from "@element-plus/icons-vue";
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
  <div class="app-shell">
    <aside v-if="api.useMock || auth.isAuthenticated.value" class="app-nav" aria-label="主导航">
      <div class="brand">
        <span class="brand-mark">SF</span>
        <div>
          <strong>SupportFlow</strong>
          <small>售后 Agent 工作台</small>
        </div>
      </div>
      <nav>
        <RouterLink to="/chat">
          <el-icon><ChatRound /></el-icon>
          用户聊天端
        </RouterLink>
        <RouterLink v-if="api.useMock || auth.isAdmin.value" to="/admin">
          <el-icon><DataAnalysis /></el-icon>
          管理员端
        </RouterLink>
      </nav>
      <div v-if="!api.useMock" class="nav-session">
        <span>{{ auth.usernameHint.value }}</span>
        <small>{{ auth.current.value?.role }}</small>
        <el-button text :icon="SwitchButton" @click="logout">退出</el-button>
      </div>
    </aside>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>
