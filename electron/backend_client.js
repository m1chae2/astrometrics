/**
 * @fileoverview The one place where Electron's main process talks to the backend.
 *
 * The backend is the Python server that does the app's real work. It offers a
 * fixed public interface, declared in `backend/public_interface.py`. Electron
 * uses two parts of it: the `/api/rpc` route, where a JSON-RPC request (a small
 * JSON message that names a method and its inputs) runs one backend method, and
 * the `/api/ready` route, which says when the backend has finished starting up.
 *
 * Every other Electron file calls the functions here instead of calling `fetch`
 * itself. An ESLint rule enforces this, and a backend test checks every path
 * this file uses against the declared interface.
 *
 * The backend's HTTP routes take no session token. Only its WebSockets need the
 * token, and the renderer opens those, so no token is attached here.
 */

/** The port the backend listens on when `ASTROMETRICS_PORT` is not set. */
const DEFAULT_BACKEND_PORT = '5000';

/** The backend route that runs one JSON-RPC method. */
const RPC_PATH = '/api/rpc';

/** The backend route that answers 200 once start-up has finished. */
const READY_PATH = '/api/ready';

/**
 * Gives the port the backend listens on.
 *
 * Reads `ASTROMETRICS_PORT` each time, so a change made by a test or by the
 * start-up code is picked up.
 *
 * @returns {string} The port number, as text.
 */
export function backendPort() {
  return process.env.ASTROMETRICS_PORT || DEFAULT_BACKEND_PORT;
}

/**
 * Gives the backend's base URL (its address without a path).
 *
 * @returns {string} For example `http://127.0.0.1:5000`.
 */
export function backendBaseUrl() {
  return `http://127.0.0.1:${backendPort()}`;
}

/**
 * Removes one trailing slash from a base URL, so a path can be added after it.
 *
 * @param {string} baseUrl A base URL, with or without a trailing slash.
 * @returns {string} The same URL without the trailing slash.
 */
function trimTrailingSlash(baseUrl) {
  return baseUrl.replace(/\/$/, '');
}

/**
 * Runs one backend method through the `/api/rpc` route.
 *
 * A network failure (no backend, or the request was cancelled) is not caught
 * here. It reaches the caller as a thrown error, so each caller keeps its own
 * way of handling it.
 *
 * @param {string} method The backend method name, such as `telescope:park`.
 * @param {object} [params] The method's inputs.
 * @param {object} [options] Request options.
 * @param {string|number} [options.id] The request id the backend echoes back.
 * @param {AbortSignal} [options.signal] Cancels the request when it fires.
 * @param {string} [options.baseUrl] Backend base URL; defaults to `backendBaseUrl()`.
 * @param {Function} [options.fetchImpl] The `fetch` function to use; defaults to the global one.
 * @returns {Promise<{ok: boolean, status: number, statusText: string, envelope: object|null}>}
 *   The HTTP result, plus the parsed JSON reply (`envelope`), or null when the
 *   reply was not JSON.
 */
export async function callRpc(method, params = {}, { id = method, signal, baseUrl, fetchImpl } = {}) {
  const send = fetchImpl || fetch;
  const url = `${trimTrailingSlash(baseUrl || backendBaseUrl())}${RPC_PATH}`;
  const init = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id, method, params }),
  };
  if (signal) init.signal = signal;
  const response = await send(url, init);
  const envelope = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, statusText: response.statusText, envelope };
}

/**
 * Asks the backend whether it has finished starting up.
 *
 * @param {object} [options] Request options.
 * @param {number} [options.timeoutMs] Give up after this many milliseconds.
 * @param {string} [options.baseUrl] Backend base URL; defaults to `backendBaseUrl()`.
 * @returns {Promise<{ok: boolean, status: number}>} The HTTP result. `ok` is
 *   true once the backend is ready. A 404 status means the backend has no
 *   ready route. Throws when nothing answers in time.
 */
export async function checkReady({ timeoutMs = 2000, baseUrl } = {}) {
  const response = await fetch(readyUrl(baseUrl), { signal: AbortSignal.timeout(timeoutMs) });
  return { ok: response.ok, status: response.status };
}

/**
 * Gives the full address of the backend's ready route, for log messages.
 *
 * @param {string} [baseUrl] Backend base URL; defaults to `backendBaseUrl()`.
 * @returns {string} For example `http://127.0.0.1:5000/api/ready`.
 */
export function readyUrl(baseUrl) {
  return `${trimTrailingSlash(baseUrl || backendBaseUrl())}${READY_PATH}`;
}
