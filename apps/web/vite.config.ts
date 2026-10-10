import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

const communityProductExtension = fileURLToPath(new URL('./src/shell/community.ts', import.meta.url))
const configuredProductExtension = process.env.ZY_PRODUCT_UI_EXTENSION?.trim()

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [vue()],
    resolve: {
        alias: {
            '@zy-product-extension': configuredProductExtension || communityProductExtension,
            '@zy-user-web': fileURLToPath(new URL('./src', import.meta.url)),
            'vue': fileURLToPath(new URL('./node_modules/vue', import.meta.url)),
            'naive-ui': fileURLToPath(new URL('./node_modules/naive-ui', import.meta.url)),
            'axios': fileURLToPath(new URL('./node_modules/axios', import.meta.url)),
        },
    },
    build: {
        target: 'esnext',
    },
    optimizeDeps: {
        esbuildOptions: {
            target: 'esnext',
        },
    },
    server: {
        host: '0.0.0.0',
        port: 3000,
        proxy: {
            // 前端统一 API 前缀 /api —— 与 deploy/nginx/nginx.conf 的
            // `location /api/` 逐条对齐：
            //   /api/v1/<path> → 原样透传（后端真实前缀就是 /api/v1）
            //   /api/<path>     → /api/v1/<path>（knowledgeSources 等不带 v1 的调用，
            //                      后端把同一份知识库路由挂在 /api/ 与 /api/v1/ 两个前缀下）
            '/api': {
                target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
                changeOrigin: true,
                rewrite: (path) => (path.startsWith('/api/v1/') ? path : path.replace(/^\/api\//, '/api/v1/')),
                ws: true,
            },
            // 原先还有 5 个前缀块（portal-api / knowledge-api / sso / aigc / admin-api），
            // 全部指向 127.0.0.1:8100 的外部服务，但 apps/web/src 里零引用
            // （grep 实测 0 个文件命中），属移植遗留的死配置 —— 已删除。
            // 若将来确实要接 SSO / 门户等外部服务，按实际需要重新添加并同步 nginx。
        }
    }
})
