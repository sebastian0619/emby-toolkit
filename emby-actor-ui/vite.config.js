// vite.config.js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { version } from './package.json'

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(version)
  },
  server: {
    proxy: {
      // API 代理保持不变
      '/api': {
        target: 'http://localhost:5257',
        changeOrigin: true,
      },
      
      // ★★★ START: 3. 新增对 /image_proxy 的代理 ★★★
      // 这个规则专门用于代理图片请求
      '/image_proxy': {
        target: 'http://localhost:5257', // 目标仍然是我们的 Python 后端
        changeOrigin: true,
        // 这里不需要路径重写，因为后端的路由就是 /image_proxy/...
      }
      // ★★★ END: 3. ★★★
    }
  }
})