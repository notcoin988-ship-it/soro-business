import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Бэкенд живёт в контейнере на 8000. Прокси нужен, чтобы в разработке
// не ловить CORS и обращаться к API по относительному /api, как в проде.
// `base` нужен для GitHub Pages: сайт живёт не в корне домена, а в
// подпапке с именем репозитория, и без префикса браузер ищет ассеты по
// `/assets/...` — то есть в корне, где их нет, и страница остаётся белой.
// Локальная разработка и обычный сервер работают с базой `/`, поэтому
// значение приходит переменной сборки, а не зашито.
export default defineConfig({
  base: process.env.VITE_BASE ?? "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
