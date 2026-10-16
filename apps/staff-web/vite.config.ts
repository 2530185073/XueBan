import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/staff/',
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { '/v1': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
  preview: { port: 4173, strictPort: true, proxy: { '/v1': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
})
