import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

// ZY-Agents 管理端。后端在 127.0.0.1:8000，路由自带 /api/v1 前缀，
// 因此 /api 代理**不做 rewrite**（原样透传到 FastAPI）。
export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      'vue': path.resolve(__dirname, './node_modules/vue'),
      'vue-router': path.resolve(__dirname, './node_modules/vue-router'),
      'pinia': path.resolve(__dirname, './node_modules/pinia'),
      'naive-ui': path.resolve(__dirname, './node_modules/naive-ui'),
      'axios': path.resolve(__dirname, './node_modules/axios'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: Number(process.env.VITE_ADMIN_PORT || 3100),
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
