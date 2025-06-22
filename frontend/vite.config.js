import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path' // ← required to resolve paths

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src') // ← this sets @ to mean ./src
    }
  },
  server: {
    proxy: {
      '/start-recon': 'http://localhost:8000',
      '/update-path': 'http://localhost:8000',
      '/session': 'http://localhost:8000',
      '/start-crawl': 'http://localhost:8000',
      '/twilio': 'http://localhost:8000',
    },
  },
})
