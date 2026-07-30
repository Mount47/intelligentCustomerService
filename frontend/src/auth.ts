import { computed, ref } from "vue";
import type { TokenResponse } from "./api/types";

const STORAGE_KEY = "supportflow.auth";

interface StoredAuth {
  accessToken: string;
  expiresAt: number;
  userId: string;
  role: "user" | "admin";
}

function loadStored(): StoredAuth | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredAuth;
    if (!parsed.accessToken || parsed.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    window.sessionStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

const current = ref<StoredAuth | null>(loadStored());

export const auth = {
  current,
  isAuthenticated: computed(() => current.value !== null && current.value.expiresAt > Date.now()),
  isAdmin: computed(() => current.value?.role === "admin"),
  usernameHint: computed(() => current.value ? `用户 #${current.value.userId}` : ""),
  token(): string | null {
    if (current.value && current.value.expiresAt <= Date.now()) this.clear();
    return current.value?.accessToken ?? null;
  },
  establish(response: TokenResponse): void {
    const value: StoredAuth = {
      accessToken: response.accessToken,
      expiresAt: Date.now() + response.expiresIn * 1000,
      userId: response.userId,
      role: response.role
    };
    current.value = value;
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  },
  clear(): void {
    current.value = null;
    window.sessionStorage.removeItem(STORAGE_KEY);
  }
};
