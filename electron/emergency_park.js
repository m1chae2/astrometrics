/**
 * Emergency park command for the tray popover.
 *
 * The popover's Park button asks the main process to park the mount. This
 * module sends the `telescope:park` JSON-RPC request to the backend and
 * reports whether the backend parked the mount. The request goes through
 * `backend_client.js`, the one module that talks to the backend.
 */

import { callRpc } from './backend_client.js';

/**
 * Parks the telescope mount through the backend's RPC endpoint.
 * @param {object} [options] - Options.
 * @param {string} [options.backendUrl] - Base URL of the backend; defaults to the one `ASTROMETRICS_PORT` names.
 * @param {Function} [options.fetchImpl] - The `fetch` function to use.
 * @returns {Promise<{ok: boolean, error?: string}>} Whether the mount was parked, and why not if it was not.
 */
export async function emergencyPark({ backendUrl, fetchImpl } = {}) {
  try {
    const { ok, status, statusText, envelope } = await callRpc('telescope:park', {}, {
      id: 'emergency-park',
      baseUrl: backendUrl,
      fetchImpl,
    });
    if (!ok || !envelope || envelope.error) {
      const reason = envelope?.error?.message || `HTTP ${status} ${statusText}`;
      return { ok: false, error: reason };
    }
    // The backend returns `true` when the mount accepted the park command.
    const parked = envelope.result?.data ?? envelope.result;
    return parked === true ? { ok: true } : { ok: false, error: 'The mount did not accept the park command.' };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}
