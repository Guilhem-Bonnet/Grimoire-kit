import { defineConfig } from 'vitest/config';

export default defineConfig({
  publicDir: '.generated/public',
  test: {
    globals: true,
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reportsDirectory: './coverage',
      reporter: ['text', 'lcov', 'json-summary'],
      include: ['src/**/*.ts'],
      exclude: ['src/index.ts'],
      thresholds: {
        lines: 65,
        functions: 65,
        statements: 65,
        branches: 55
      }
    }
  }
});