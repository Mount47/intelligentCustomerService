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
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/orders";
    await router.replace(session.role === "admin" && redirect === "/orders" ? "/admin" : redirect);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "登录失败";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section class="login-page">
    <div class="login-frame">
      <div class="login-story">
        <div class="login-brand">
          <span class="brand-mark">SF</span>
          <strong>SupportFlow</strong>
        </div>
        <div>
          <p class="eyebrow">订单售后服务</p>
          <h1>从您的订单开始，<br />把售后说清楚。</h1>
          <p>登录后可直接选择自己的订单，再咨询退款、退货或物流问题，无需手动输入订单号。</p>
        </div>
        <ol class="login-steps" aria-label="售后流程">
          <li><span>01</span>选择订单</li>
          <li><span>02</span>说明问题</li>
          <li><span>03</span>查看处理结果</li>
        </ol>
      </div>

      <div class="login-card">
        <p class="eyebrow">欢迎回来</p>
        <h2>登录账号</h2>
        <p class="login-copy">登录后仅能查看和处理您自己的订单。</p>
        <form class="login-form" @submit.prevent="submit">
          <label>
            <span>用户名</span>
            <el-input v-model="username" size="large" autocomplete="username" placeholder="请输入用户名" :prefix-icon="User" />
          </label>
          <label>
            <span>密码</span>
            <el-input
              v-model="password"
              size="large"
              type="password"
              autocomplete="current-password"
              show-password
              placeholder="请输入密码"
              :prefix-icon="Lock"
            />
          </label>
          <el-button type="primary" size="large" native-type="submit" :loading="loading">登录</el-button>
        </form>
        <el-alert v-if="error" title="用户名或密码不正确，请重新输入" type="error" show-icon :closable="false" />
        <div class="login-demo">
          <strong>本地演示账号</strong>
          <span>普通用户：demo / supportflow-user</span>
          <span>管理员：admin / supportflow-admin</span>
        </div>
      </div>
    </div>
  </section>
</template>
