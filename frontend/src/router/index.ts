import { createRouter, createWebHistory } from "vue-router";
import ChatView from "../views/ChatView.vue";
import AdminView from "../views/AdminView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    { path: "/chat", component: ChatView },
    { path: "/admin", component: AdminView }
  ]
});
