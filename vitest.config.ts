/**
 * @fileoverview Vitest configuration for unit and integration testing.
 */

import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Run tests in node environment by default (utilities/logic focused)
    // Run tests in jsdom environment to support React components
    environment: 'jsdom',
    globals: true,
    // Only include the unit tests folder pattern. Exclude Playwright specs.
    include: ['tests/unit/**/*.test.{ts,tsx,js}', 'tests/test_*.{ts,tsx}', 'ui/**/tests/test_*.{ts,tsx}'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/*.spec.{ts,tsx}', '**/.venv/**'],
    setupFiles: ['./ui/setupTests.ts'],
    // Note: `test.deps.inline` is deprecated in recent Vitest versions.
    // Use `server.deps.inline` below to inline project source during
    // vite-node execution (keeps transforms consistent for vitest).
    // Provide a clear output for CI
    reporters: 'default',
    coverage: {
      enabled: true,
      provider: 'v8',
      reporter: ['text', 'json', 'html', 'json-summary', 'lcov'],
      reportsDirectory: './coverage',
      include: ['ui/**/*.{ts,tsx}'],
      exclude: ['**/tests/**', '**/setupTests.ts']
    }
  }
  ,
  server: {
    // @ts-expect-error -- 'deps' is a valid Vitest config option but not present in Vite's ServerOptions type
    deps: {
      inline: [/ui/]
    },
    watch: {
      ignored: ['**/.venv/**', '**/node_modules/**', '**/dist/**', '**/out/**']
    }
  }
});
