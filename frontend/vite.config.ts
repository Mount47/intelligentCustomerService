import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/echarts/") || id.includes("/zrender/")) return "charts";
          if (id.includes("/element-plus/") || id.includes("/@element-plus/")) return "element";
          if (id.includes("/vue/") || id.includes("/vue-router/")) return "vue";
          return "vendor";
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true
      }
    }
  }
});
