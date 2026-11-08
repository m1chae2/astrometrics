/**
 * @fileoverview Vite settings for serving the production UI build during a TPM run.
 *
 * The page is served by `vite preview` on a random port. The backend only
 * accepts a WebSocket handshake from its own app origin, so the preview server
 * forwards the backend's routes to the throwaway backend and sets that origin
 * on the way (the real app does the same with its `app://` protocol). The UI is
 * then pointed at the preview server's own address.
 *
 * Settings come from environment variables so the runner chooses the ports:
 * `TPM_UI_DIST` (the build folder), `TPM_UI_PORT` (preview port) and
 * `TPM_BACKEND_ORIGIN` (the throwaway backend, never port 5000).
 */

import path from 'node:path';
import { defineConfig } from 'vite';

const backendOrigin = process.env.TPM_BACKEND_ORIGIN ?? '';
const backendPort = Number(new URL(backendOrigin || 'http://invalid.invalid').port);
if (!backendOrigin || backendPort === 5000) {
    throw new Error(`TPM_BACKEND_ORIGIN must name the throwaway backend and never port 5000 (got "${backendOrigin}").`);
}

/** The origin the backend accepts for the app's own windows. */
const APP_ORIGIN = 'app://.';

/**
 * Builds one proxy rule that sends a path prefix to the throwaway backend.
 *
 * @param {boolean} webSocket - Whether the prefix carries WebSocket upgrades.
 * @return {object} A `vite preview` proxy entry.
 */
function backendRule(webSocket) {
    return { target: backendOrigin, changeOrigin: false, ws: webSocket, headers: { Origin: APP_ORIGIN } };
}

export default defineConfig({
    root: path.resolve(import.meta.dirname, '..', '..', 'ui'),
    base: './',
    preview: {
        host: '127.0.0.1',
        port: Number(process.env.TPM_UI_PORT),
        strictPort: true,
        proxy: {
            '/api': backendRule(false),
            '/ws': backendRule(true),
            '/static': backendRule(false),
            '/figure': backendRule(false),
            '/mpl_static': backendRule(false),
            '/_images': backendRule(false),
        },
    },
    build: { outDir: process.env.TPM_UI_DIST },
});
