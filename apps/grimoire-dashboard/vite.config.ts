import { defineConfig } from 'vite';
import path from 'node:path';

// Grimoire Dashboard — live local control plane.
// Runs on :4174 by default to avoid clashing with the cockpit demo (:5173).
export default defineConfig({
  root: path.resolve(__dirname, 'app'),
  publicDir: path.resolve(__dirname, 'public'),
  resolve: {
    alias: {
      '@game': path.resolve(__dirname, '../grimoire-game/src'),
      '@dashboard': path.resolve(__dirname, 'src')
    }
  },
  build: {
    outDir: path.resolve(__dirname, 'dist'),
    emptyOutDir: true,
    sourcemap: true
  },
  server: {
    port: 4174,
    strictPort: true,
    proxy: {
      '/ws': {
        target: 'ws://localhost:4175',
        ws: true,
        changeOrigin: true
      }
    }
  }
});
