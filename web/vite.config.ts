import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

declare const process: { env: Record<string, string | undefined> };

const apiOrigin = process.env.VITE_API_ORIGIN ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiOrigin,
        changeOrigin: false,
      },
    },
  },
});
