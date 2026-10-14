/**
 * @fileoverview Unit tests for the Electron main-process emergency park command.
 * Verifies that it posts the `telescope:park` JSON-RPC request to `/api/rpc` and
 * reports every kind of failure instead of ignoring it.
 */

import { describe, it, expect, vi } from 'vitest';
import { emergencyPark } from '../../electron/emergency_park.js';

/** Builds a fake fetch that answers with the given HTTP status and JSON body. */
function fakeFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: async () => body,
  });
}

describe('emergencyPark', () => {
  /** The request goes to the RPC endpoint with the park method. */
  it('posts telescope:park to /api/rpc', async () => {
    const fetchImpl = fakeFetch(200, { jsonrpc: '2.0', id: 'emergency-park', result: true });

    const result = await emergencyPark({ backendUrl: 'http://127.0.0.1:5000/', fetchImpl });

    expect(result).toEqual({ ok: true });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:5000/api/rpc');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toMatchObject({ jsonrpc: '2.0', method: 'telescope:park', params: {} });
  });

  /** A result wrapped in a data field still counts. */
  it('accepts a result wrapped in a data field', async () => {
    const fetchImpl = fakeFetch(200, { result: { data: true } });
    expect(await emergencyPark({ fetchImpl })).toEqual({ ok: true });
  });

  /** A JSON-RPC error is reported with its message. */
  it('reports a JSON-RPC error', async () => {
    const fetchImpl = fakeFetch(500, { error: { code: -32603, message: 'Internal error: mount offline' } });
    expect(await emergencyPark({ fetchImpl })).toEqual({ ok: false, error: 'Internal error: mount offline' });
  });

  /** A reply of false means the mount did not accept the command. */
  it('reports a false result', async () => {
    const fetchImpl = fakeFetch(200, { result: false });
    const result = await emergencyPark({ fetchImpl });
    expect(result.ok).toBe(false);
  });

  /** A network failure is reported, not thrown. */
  it('reports a network failure', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('connect ECONNREFUSED'));
    expect(await emergencyPark({ fetchImpl })).toEqual({ ok: false, error: 'connect ECONNREFUSED' });
  });
});
