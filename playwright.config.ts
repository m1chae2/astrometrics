/**
 * @fileoverview Configuration for Playwright E2E testing.
 */

import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: '.',
    testMatch: '**/*.spec.ts',
    timeout: 30000,
    retries: 0,
    reporter: 'list',
    use: {
        trace: 'on-first-retry',
    },
    outputDir: 'test-results/',
});
