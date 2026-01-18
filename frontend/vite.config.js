import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:5006',
      '/model_settings': 'http://localhost:5006',
      '/login': 'http://localhost:5006',
      '/logout': 'http://localhost:5006',
      '/static': 'http://localhost:5006'
    }
  },
  preview: {
    port: 5174,
    strictPort: true
  },
  build: {
    outDir: 'dist'
  }
});
