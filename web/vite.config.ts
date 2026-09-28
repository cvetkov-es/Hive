import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Сборка кладётся в web/dist и коммитится: README обещает запуск одной командой,
// без установки Node. Разработка ходит в бэкенд через прокси, чтобы адрес API
// в коде был один и тот же и в разработке, и в собранном виде.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
