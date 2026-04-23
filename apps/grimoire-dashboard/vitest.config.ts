import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
  resolve: {
    alias: {
      '@game': path.resolve(__dirname, '../grimoire-game/src'),
      '@dashboard': path.resolve(__dirname, 'src')
    }
  },
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    globals: true
  }
});
