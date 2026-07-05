import { defineConfig } from 'vite';

// 开发模式：Vite 起前端（默认 5173），/api 代理到后端（finance-agent --web，8765）。
// 生产模式：`npm run build` 产出 dist/，由后端直接服务（web/app.py 的 index 路由）。
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': `http://127.0.0.1:${process.env.BACKEND_PORT || 8765}`,
    },
  },
});
