<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Lock, User } from "@element-plus/icons-vue";
import { api } from "../api/client";
import { auth } from "../auth";

const route = useRoute();
const router = useRouter();
const username = ref("demo");
const password = ref("supportflow-user");
const loading = ref(false);
const error = ref("");

async function submit() {
  if (!username.value.trim() || !password.value || loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const session = await api.login(username.value.trim(), password.value);
    auth.establish(session);
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/chat";
    await router.replace(session.role === "admin" && redirect === "/chat" ? "/admin" : redirect);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "登录失败";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section class="login-page">
    <div class="login-card">
      <p class="eyebrow">SupportFlow · Secure Workspace</p>
      <h1>登录售后工作台</h1>
      <p class="login-copy">身份由后端签发的短期 Bearer Token 确定，不信任前端传入的用户编号。</p>
      <form class="login-form" @submit.prevent="submit">
        <el-input v-model="username" size="large" autocomplete="username" placeholder="用户名" :prefix-icon="User" />
        <el-input
          v-model="password"
          size="large"
          type="password"
          autocomplete="current-password"
          show-password
          placeholder="密码"
          :prefix-icon="Lock"
        />
        <el-button type="primary" size="large" native-type="submit" :loading="loading">登录</el-button>
      </form>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <div class="login-demo">
        <span>用户演示：demo / supportflow-user</span>
        <span>管理员：admin / supportflow-admin</span>
      </div>
    </div>
  </section>
</template>
