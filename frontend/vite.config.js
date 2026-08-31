import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 👇 关键配置：拦截所有 /api 开头的请求
      '/api': {
        target: 'http://localhost:8000', // 转发到你的 Python 后端
        changeOrigin: true,             // 允许跨域
        // secure: false,               // 如果是 https 可以打开
      }
    }
  }
})
