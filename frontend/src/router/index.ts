import { createRouter, createWebHistory } from "vue-router";
import { auth } from "../auth";

const LoginView = () => import("../views/LoginView.vue");
const ChatView = () => import("../views/ChatView.vue");
const AdminView = () => import("../views/AdminView.vue");
const useMock = import.meta.env.VITE_USE_MOCK === "true"
  || (import.meta.env.VITE_USE_MOCK == null && import.meta.env.DEV);

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    { path: "/login", component: LoginView, meta: { public: true } },
    { path: "/chat", component: ChatView },
    { path: "/admin", component: AdminView, meta: { requiresAdmin: true } }
  ]
});

router.beforeEach((to) => {
  if (useMock || to.meta.public) {
    if (to.path === "/login" && auth.isAuthenticated.value) return "/chat";
    return true;
  }
  if (!auth.isAuthenticated.value) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }
  if (to.meta.requiresAdmin && !auth.isAdmin.value) return "/chat";
  if (to.path === "/login") return auth.isAdmin.value ? "/admin" : "/chat";
  return true;
});
