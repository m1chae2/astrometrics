/**
 * Emergency park command for the tray popover.
 *
 * The popover's Park button asks the main process to park the mount. This
 * module sends the `telescope:park` JSON-RPC request to the backend and
 * reports whether the backend parked the mount.
 */

/**
 * Parks the telescope mount through the backend's RPC endpoint.
 * @param {object} [options] - Options.
 * @param {string} [options.backendUrl] - Base URL of the backend.
 * @param {Function} [options.fetchImpl] - The `fetch` function to use.
 * @returns {Promise<{ok: boolean, error?: string}>} Whether the mount was parked, and why not if it was not.
 */
export async function emergencyPark({
  backendUrl = 'http://127.0.0.1:5000',
  fetchImpl = fetch,
} = {}) {
  try {
    const response = await fetchImpl(`${backendUrl.replace(/\/$/, '')}/api/rpc`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 'emergency-park', method: 'telescope:park', params: {} }),
    });
    const envelope = await response.json().catch(() => null);
    if (!response.ok || !envelope || envelope.error) {
      const reason = envelope?.error?.message || `HTTP ${response.status} ${response.statusText}`;
      return { ok: false, error: reason };
    }
    // The backend returns `true` when the mount accepted the park command.
    const parked = envelope.result?.data ?? envelope.result;
    return parked === true ? { ok: true } : { ok: false, error: 'The mount did not accept the park command.' };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}
