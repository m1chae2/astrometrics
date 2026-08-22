/**
 * @fileoverview Vite configuration for building and serving the React application.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createHtmlPlugin } from 'vite-plugin-html';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const isProd = mode === 'production';
  // Strict CSP for production, Lax for Dev (HMR requires ws:)
  // backend runs on 127.0.0.1:5000, so we must allow it.
  const cspContent = isProd
    ? "<!-- Production CSP --> <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; script-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; connect-src 'self' https: http://127.0.0.1:* ws://127.0.0.1:*; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self';\">"
    : "<!-- Dev CSP --> <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; connect-src 'self' http: ws: data:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; child-src 'self' blob:; img-src 'self' data: blob: http:; style-src 'self' 'unsafe-inline';\">";

  return {
    plugins: [
      react(),
      createHtmlPlugin({
        minify: isProd,
        inject: {
          data: {
            csp: cspContent
          }
        }
      })
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
