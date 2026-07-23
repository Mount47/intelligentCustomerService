/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_USE_MOCK?: "true" | "false";
  readonly VITE_ENABLE_MOCK_FALLBACK?: "true" | "false";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
