import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'path';

export default defineConfig({
  plugins: [svelte()],
  build: {
    // Build directly into backend's static-mount path so FastAPI serves the SPA
    outDir: resolve(__dirname, '../backend/ft8_appliance/web/static'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      // During dev, proxy API to the running backend on :8000
      '/api': 'http://localhost:8000',
      '/sse': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
