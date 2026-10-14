/**
 * @fileoverview Vite configuration for building and serving the React application.
 *
 * Supports multi-page build: the main app (index.html) and the system tray
 * popover (tray_popover.html) are built as separate Rollup entry points while
 * sharing the same vendor chunk split.
 *
 * CSP injection is handled by the inline `injectCspPlugin`, which replaces the
 * <%- csp %> EJS placeholder in every HTML file during both dev and build,
 * without requiring vite-plugin-html's per-page configuration overhead.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const isProd = mode === 'production';
  // Strict CSP for production, Lax for Dev (HMR requires ws:)
  // backend runs on 127.0.0.1:5000, so we must allow it.
  const cspContent = isProd
    ? "<!-- Production CSP --> <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; script-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; connect-src 'self' https: http://127.0.0.1:* ws://127.0.0.1:*; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self';\">"
    : "<!-- Dev CSP --> <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; connect-src 'self' http: ws: data:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; child-src 'self' blob:; img-src 'self' data: blob: http:; style-src 'self' 'unsafe-inline';\">";

  /**
   * Inline Vite plugin that replaces the <%- csp %> EJS placeholder in any
   * HTML file with the resolved CSP meta tag. Applied to all HTML files so
   * both index.html and tray_popover.html receive injection without requiring
   * vite-plugin-html's per-page configuration.
   */
  const injectCspPlugin = {
    name: 'inject-csp',
    transformIndexHtml(html) {
      return html.replace(/<%- csp %>/g, cspContent);
    },
  };

  return {
    plugins: [
      react(),
      injectCspPlugin,
    ],
    resolve: {
      alias: [
        // Match the longer path first to avoid Vite appending suffixes like
        // '/promises' to the replacement file path.
        { find: 'node:fs/promises', replacement: path.resolve(__dirname, 'ui', 'common', 'utils', 'nodeFsShim.js') },
        { find: 'node:fs', replacement: path.resolve(__dirname, 'ui', 'common', 'utils', 'nodeFsShim.js') },
        { find: 'fs/promises', replacement: path.resolve(__dirname, 'ui', 'common', 'utils', 'nodeFsShim.js') },
        { find: 'fs', replacement: path.resolve(__dirname, 'ui', 'common', 'utils', 'nodeFsShim.js') }
      ]
    },
    server: {
      // In regular dev we bind to loopback. If DEV_LAN=1 is set the dev server
      // should be LAN-accessible for testing from other devices (e.g., a tablet).
      host: (typeof process !== 'undefined' && process.env && process.env.DEV_LAN === '1') ? "0.0.0.0" : "127.0.0.1",
      port: (typeof process !== 'undefined' && process.env && process.env.DEV_PORT) ? parseInt(process.env.DEV_PORT, 10) : 5173,
      // don't watch large folders like Python virtualenvs or node_modules to avoid inotify limits
      watch: {
        ignored: ['**/.venv/**', '**/node_modules/**', '**/dist/**', '**/out/**']
      }
    },
    root: 'ui',
    publicDir: '../public',
    build: {
      outDir: '../dist',
      emptyOutDir: true,
      rollupOptions: {
        input: {
          main: path.resolve(__dirname, 'ui/index.html'),
          tray_popover: path.resolve(__dirname, 'ui/tray_popover.html'),
        },
        output: {
          manualChunks: {
            'vendor': ['react', 'react-dom'],
            'plotly': ['plotly.js-dist-min'],
            'fits': ['jsfitsio']
          }
        }
      },
      chunkSizeWarningLimit: 2000
    },
    base: './',
  };
});
