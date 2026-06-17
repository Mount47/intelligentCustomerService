<script setup lang="ts">
import { ChatRound, DataAnalysis } from "@element-plus/icons-vue";
</script>

<template>
  <div class="app-shell">
    <aside class="app-nav" aria-label="主导航">
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
        <RouterLink to="/admin">
          <el-icon><DataAnalysis /></el-icon>
          管理员端
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>
