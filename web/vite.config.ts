import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

declare const process: { env: Record<string, string | undefined> };

function port(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > 65535) throw new Error(`${name}=${raw} 不是有效端口`);
  return value;
}

// 端口可用环境变量修改：FRONTEND_PORT（默认 5173）、BACKEND_PORT（默认 8001）。
// 代理目标默认跟随 BACKEND_PORT，VITE_API_ORIGIN 可整体覆盖。
const frontendPort = port("FRONTEND_PORT", 5173);
const apiOrigin = process.env.VITE_API_ORIGIN || `http://127.0.0.1:${port("BACKEND_PORT", 8001)}`;

export default defineConfig({
  plugins: [vue()],
  server: {
    port: frontendPort,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiOrigin,
        changeOrigin: false,
      },
    },
  },
});
