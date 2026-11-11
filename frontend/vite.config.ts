import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/mcp': { target: 'http://127.0.0.1:8000' },
      '/oauth': { target: 'http://127.0.0.1:8000' },
      '/.well-known': { target: 'http://127.0.0.1:8000' },
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
});
