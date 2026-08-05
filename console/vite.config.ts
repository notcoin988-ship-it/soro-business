import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Бэкенд живёт в контейнере на 8000. Прокси нужен, чтобы в разработке
// не ловить CORS и обращаться к API по относительному /api, как в проде.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
