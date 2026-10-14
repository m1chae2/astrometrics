/**
 * @fileoverview Checks what a figure window may show.
 *
 * The app's Python terminal can draw Matplotlib plots (charts). The backend
 * then offers each plot in two ways: an interactive page at `/figure/<number>`,
 * and a PNG picture saved in its scratch folder (`~/.cache/astrometrics`, or
 * `$XDG_CACHE_HOME/astrometrics` when that is set). The renderer asks the main
 * process to open a window for a plot, and passes the page address, the PNG
 * path, or both.
 *
 * The renderer must not be able to open any web page or read any file this
 * way. So this module accepts only:
 *
 * 1. a `/figure/<number>` page on the backend's own address, and
 * 2. a `.png` file whose real location (after following links) is inside the
 *    backend's scratch folder.
 *
 * Anything else is refused, with a reason the caller logs.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { backendBaseUrl } from './backend_client.js';

/**
 * The path of one interactive figure page. `backend/public_interface.py`
 * declares it as `/figure/{figure_id}`, where the id is a whole number.
 */
const FIGURE_PAGE_PATH = new RegExp('^/figure/[0-9]+$');

/**
 * Gives the folder where the backend saves plot pictures.
 *
 * This matches `scratch_directory()` in `astrometricslib/foundation/paths.py`,
 * which the backend's scripting service uses for its PNG files.
 *
 * @returns {string} The folder's path.
 */
export function plotScratchDirectory() {
  const cacheRoot = process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache');
  return path.join(cacheRoot, 'astrometrics');
}

/**
 * Checks a figure page address and gives the full address to load.
 *
 * Accepts a path such as `/figure/3`, or a full address on the backend's own
 * origin (its scheme, host and port). The path must be exactly `/figure/`
 * followed by a number, with no query or `#` part.
 *
 * @param {unknown} candidate The address the renderer sent.
 * @param {string} [baseUrl] The backend's base URL; defaults to `backendBaseUrl()`.
 * @returns {string|null} The full address to load, or null when it is refused.
 */
export function resolveFigureUrl(candidate, baseUrl = backendBaseUrl()) {
  if (typeof candidate !== 'string' || !candidate) return null;
  let url;
  let base;
  try {
    base = new URL(baseUrl);
    url = new URL(candidate, base);
  } catch {
    return null;
  }
  if (url.origin !== base.origin) return null;
  if (url.search || url.hash) return null;
  if (!FIGURE_PAGE_PATH.test(url.pathname)) return null;
  return `${base.origin}${url.pathname}`;
}

/**
 * Checks a plot picture path and gives its real path.
 *
 * The file must exist, end in `.png`, and sit inside the scratch folder once
 * both paths have their links followed (`fs.realpathSync`). Following links
 * stops a link inside the folder from pointing at a file somewhere else.
 *
 * @param {unknown} candidate The file path the renderer sent.
 * @param {string} [directory] The allowed folder; defaults to `plotScratchDirectory()`.
 * @returns {string|null} The real path to read, or null when it is refused.
 */
export function resolvePlotPath(candidate, directory = plotScratchDirectory()) {
  if (typeof candidate !== 'string' || !candidate || !path.isAbsolute(candidate)) return null;
  if (path.extname(candidate).toLowerCase() !== '.png') return null;
  let realDirectory;
  let realFile;
  try {
    realDirectory = fs.realpathSync(directory);
    realFile = fs.realpathSync(candidate);
    if (!fs.statSync(realFile).isFile()) return null;
  } catch {
    return null;
  }
  if (path.extname(realFile).toLowerCase() !== '.png') return null;
  const relative = path.relative(realDirectory, realFile);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) return null;
  return realFile;
}

/**
 * Sorts a figure window request into a page address and a picture path, and
 * checks both.
 *
 * A text request that starts with `http://`, `https://` or `/figure/` is a
 * page address. Any other text is a picture path. An object may carry both,
 * as `interactiveUrl` and `plotPath`.
 *
 * @param {unknown} target What the renderer sent.
 * @param {{baseUrl?: string, directory?: string}} [options] Overrides for tests.
 * @returns {{interactiveUrl: string|null, plotPath: string|null, rejected: string[]}}
 *   The accepted page address and picture path (null when absent or refused),
 *   and one message for each part that was refused.
 */
export function resolveFigureTarget(target, { baseUrl, directory } = {}) {
  let requestedUrl = '';
  let requestedPath = '';
  if (typeof target === 'string') {
    if (/^https?:\/\//i.test(target) || target.startsWith('/figure/')) {
      requestedUrl = target;
    } else {
      requestedPath = target;
    }
  } else if (target && typeof target === 'object') {
    requestedUrl = target.interactiveUrl || '';
    requestedPath = target.plotPath || '';
  }

  const rejected = [];
  const interactiveUrl = requestedUrl ? resolveFigureUrl(requestedUrl, baseUrl) : null;
  if (requestedUrl && !interactiveUrl) {
    rejected.push(`Refused figure address ${JSON.stringify(requestedUrl)}: only the backend's own /figure/<number> pages may open.`);
  }
  const plotPath = requestedPath ? resolvePlotPath(requestedPath, directory) : null;
  if (requestedPath && !plotPath) {
    rejected.push(`Refused figure file ${JSON.stringify(requestedPath)}: only existing .png files in the backend's scratch folder may open.`);
  }
  return { interactiveUrl, plotPath, rejected };
}
