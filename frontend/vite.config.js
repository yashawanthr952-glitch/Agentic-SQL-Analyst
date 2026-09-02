import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_URL wins when set (docker-compose sets it); otherwise the dev server
// proxies /api to the backend so the browser sees a same-origin request.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
