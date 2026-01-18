import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';

const loadEnvFile = (filePath) => {
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    return raw.split('\n').reduce((acc, line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) return acc;
      const [key, ...rest] = trimmed.split('=');
      if (!key) return acc;
      const value = rest.join('=').trim().replace(/^"(.*)"$/, '$1').replace(/^'(.*)'$/, '$1');
      acc[key] = value;
      return acc;
    }, {});
  } catch (_err) {
    return {};
  }
};

const secretsPath = path.resolve(__dirname, '../secrets.env');
const secrets = loadEnvFile(secretsPath);
const backendTarget = process.env.IOT_AGENT_API_BASE_URL || secrets.IOT_AGENT_API_BASE_URL || '';
const proxyTarget = backendTarget || undefined;

const proxy = proxyTarget
  ? {
      '/api': proxyTarget,
      '/model_settings': proxyTarget,
      '/login': proxyTarget,
      '/logout': proxyTarget,
      '/static': proxyTarget,
      '/config.js': proxyTarget
    }
  : {};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy
  },
  preview: {
    port: 5174,
    strictPort: true
  },
  build: {
    outDir: 'dist'
  }
});
