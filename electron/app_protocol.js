/**
 * @fileoverview The private `app://` address the packaged window loads the built UI from.
 *
 * A page loaded from a file sends the WebSocket origin `file://`, which the backend refuses. A page
 * loaded from `app://./index.html` sends `app://.`, which the backend allows. This file holds the
 * safe path lookup (no file outside the UI folder is ever served), the file types, and the handler
 * that Electron's `protocol.handle` calls.
 */
import fs from 'fs';
import path from 'path';

/** The scheme name; the page address is `app://./index.html`. */
export const APP_SCHEME = 'app';

/** The host part of every app address. The backend allows the origin `app://.`, so it must be `.`. */
export const APP_HOST = '.';

/** The address the main window loads. */
export const APP_INDEX_URL = `${APP_SCHEME}://${APP_HOST}/index.html`;

/** Scheme privileges: a normal, secure web origin that can use fetch, CORS and streaming. */
export const APP_SCHEME_PRIVILEGES = {
  scheme: APP_SCHEME,
  privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
};

/** File types the built UI contains, by lower-case extension. */
const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.wasm': 'application/wasm',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
};

/**
 * Gives the content type for a file name.
 *
 * @param {string} filePath File name or path.
 * @returns {string} The MIME type, or `application/octet-stream` for an unknown extension.
 */
export function mimeTypeFor(filePath) {
  return MIME_TYPES[path.extname(filePath).toLowerCase()] ?? 'application/octet-stream';
}

/**
 * Finds the file an app address names, staying inside the UI folder.
 *
 * Refuses a wrong scheme or host, an encoded or plain `..` segment, a backslash, a NUL character,
 * and a path that leaves the folder (also through a symbolic link). A missing file with no file
 * extension falls back to `index.html` only for a navigation request, so a page reload on a
 * client-side route works while a missing script or image stays a 404.
 *
 * @param {string} distRoot Folder holding the built UI.
 * @param {string} requestUrl The `app://` address that was requested.
 * @param {boolean} isNavigation True when the request loads a page (not a script, image or fetch).
 * @returns {{ filePath: string|null, status: number }} The file to send with 200, or null with 400/403/404.
 */
export function resolveAppFile(distRoot, requestUrl, isNavigation) {
  let parsed;
  try {
    parsed = new URL(requestUrl);
  } catch {
    return { filePath: null, status: 400 };
  }
  if (parsed.protocol !== `${APP_SCHEME}:` || parsed.hostname !== APP_HOST) {
    return { filePath: null, status: 403 };
  }
  let pathname;
  try {
    pathname = decodeURIComponent(parsed.pathname);
  } catch {
    return { filePath: null, status: 400 };
  }
  if (pathname.includes('\0') || pathname.includes('\\')) return { filePath: null, status: 400 };
  const segments = pathname.split('/').filter((segment) => segment !== '');
  if (segments.some((segment) => segment === '..')) return { filePath: null, status: 403 };

  // The files below are checked by their real path, so the folder must be too (it may be a symbolic link).
  let root = path.resolve(distRoot);
  try {
    root = fs.realpathSync(root);
  } catch {
    // Keep the resolved path; every file lookup then fails as a 404.
  }
  const insideRoot = (candidate) => candidate === root || candidate.startsWith(root + path.sep);
  const isFile = (candidate) => {
    try {
      // A symbolic link must still point inside the folder.
      const real = fs.realpathSync(candidate);
      return insideRoot(real) && fs.statSync(real).isFile();
    } catch {
      return false;
    }
  };

  const requested = path.resolve(root, ...segments);
  if (!insideRoot(requested)) return { filePath: null, status: 403 };
  const wanted = segments.length === 0 ? path.join(root, 'index.html') : requested;
  if (isFile(wanted)) return { filePath: wanted, status: 200 };

  const hasExtension = path.extname(segments[segments.length - 1] ?? '') !== '';
  if (isNavigation && !hasExtension) {
    const index = path.join(root, 'index.html');
    if (isFile(index)) return { filePath: index, status: 200 };
  }
  return { filePath: null, status: 404 };
}

/**
 * Builds the function Electron's `protocol.handle('app', ...)` calls for each request.
 *
 * @param {string} distRoot Folder holding the built UI.
 * @returns {(request: Request) => Promise<Response>} The request handler.
 */
export function createAppProtocolHandler(distRoot) {
  return async (request) => {
    const isNavigation = request.mode === 'navigate' || request.headers.get('sec-fetch-mode') === 'navigate';
    const { filePath, status } = resolveAppFile(distRoot, request.url, isNavigation);
    if (!filePath) {
      return new Response(status === 404 ? 'Not found' : 'Refused', { status, headers: { 'content-type': 'text/plain' } });
    }
    try {
      const body = await fs.promises.readFile(filePath);
      return new Response(body, { status: 200, headers: { 'content-type': mimeTypeFor(filePath) } });
    } catch {
      return new Response('Not found', { status: 404, headers: { 'content-type': 'text/plain' } });
    }
  };
}

/**
 * Decides where the UI is loaded from.
 *
 * @param {boolean} isDev True when running from source (not packaged).
 * @param {NodeJS.ProcessEnv} environment Process environment.
 * @param {string} packagedDist The `dist` folder inside the packaged app.
 * @returns {{ mode: 'dev'|'app', distRoot: string|null, url: string }} Dev server address, or the app address with its folder.
 *   `ASTROMETRICS_UI_DIST` makes a source run serve a built UI folder the way the packaged app does.
 */
export function chooseUiSource(isDev, environment, packagedDist) {
  const builtFolder = environment.ASTROMETRICS_UI_DIST;
  if (builtFolder) return { mode: 'app', distRoot: path.resolve(builtFolder), url: APP_INDEX_URL };
  if (isDev) return { mode: 'dev', distRoot: null, url: environment.ELECTRON_RENDERER_URL || 'http://127.0.0.1:5173' };
  return { mode: 'app', distRoot: packagedDist, url: APP_INDEX_URL };
}
