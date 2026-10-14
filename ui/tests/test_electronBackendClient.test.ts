/**
 * @fileoverview Unit tests for electron/backend_client.js, the one module
 * Electron's main process uses to reach the backend. Checks that it builds the
 * backend address from ASTROMETRICS_PORT, sends JSON-RPC requests to /api/rpc,
 * and asks /api/ready whether start-up has finished.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { backendBaseUrl, callRpc, checkReady, readyUrl } from '../../electron/backend_client.js';

/** Builds a fake fetch reply with the given status and JSON body. */
function reply(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: async () => body,
  };
}

describe('backend_client', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  /** With no ASTROMETRICS_PORT the backend is on port 5000. */
  it('defaults to port 5000', () => {
    vi.stubEnv('ASTROMETRICS_PORT', '');
    expect(backendBaseUrl()).toBe('http://127.0.0.1:5000');
  });

  /** ASTROMETRICS_PORT changes the address of every request. */
  it('uses ASTROMETRICS_PORT for the address', async () => {
    vi.stubEnv('ASTROMETRICS_PORT', '5123');
    fetchMock.mockResolvedValue(reply(200, { result: true }));

    await callRpc('telescope:park');

    expect(backendBaseUrl()).toBe('http://127.0.0.1:5123');
    expect(readyUrl()).toBe('http://127.0.0.1:5123/api/ready');
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:5123/api/rpc');
  });

  /** callRpc posts a JSON-RPC request and returns the parsed reply. */
  it('posts a JSON-RPC request and returns the reply', async () => {
    fetchMock.mockResolvedValue(reply(200, { jsonrpc: '2.0', id: 'a', result: { data: [1] } }));

    const result = await callRpc('terminal:completions', { text: 'x' }, { id: 'a', baseUrl: 'http://127.0.0.1:5000/' });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:5000/api/rpc');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body)).toEqual({ jsonrpc: '2.0', id: 'a', method: 'terminal:completions', params: { text: 'x' } });
    expect(result).toEqual({ ok: true, status: 200, statusText: 'OK', envelope: { jsonrpc: '2.0', id: 'a', result: { data: [1] } } });
  });

  /** A reply that is not JSON gives a null envelope instead of throwing. */
  it('gives a null envelope for a reply that is not JSON', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 502, statusText: 'Bad Gateway', json: async () => { throw new Error('bad'); } });

    const result = await callRpc('telescope:park');

    expect(result.envelope).toBeNull();
    expect(result.status).toBe(502);
  });

  /** A network failure reaches the caller as a thrown error. */
  it('lets a network failure reach the caller', async () => {
    fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));
    await expect(callRpc('telescope:park')).rejects.toThrow('ECONNREFUSED');
  });

  /** checkReady asks /api/ready with a time limit and reports the status. */
  it('checks the ready route with a time limit', async () => {
    fetchMock.mockResolvedValue(reply(503, {}));

    const result = await checkReady({ timeoutMs: 1000 });

    expect(result).toEqual({ ok: false, status: 503 });
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:5000/api/ready');
    expect(fetchMock.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  });
});
