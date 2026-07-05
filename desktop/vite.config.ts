import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const runtimeProxyTarget =
  process.env.VITE_RUNTIME_PROXY_TARGET || process.env.VITE_RUNTIME_BASE_URL || "http://127.0.0.1:43217";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: runtimeProxyTarget,
        changeOrigin: true,
        ws: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "electron/**/*.test.ts"],
  },
});
