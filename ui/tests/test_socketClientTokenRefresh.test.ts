/**
 * @fileoverview Unit tests for the event socket's session-token recovery.
 * A backend restart mints a new session token, so a page opened earlier keeps
 * presenting the old one. These tests check that a socket refused before it
 * ever opened makes the client fetch a fresh token, and that a socket that did
 * open keeps its token.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { socketClient } from '../common/utils/socketClient';

/** A stand-in WebSocket that records its URL and lets the test fire events. */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((error: unknown) => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(): void {}
  close(): void {}
}

/** Lets pending promise callbacks (the token fetch) run. */
async function flushPromises(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0);
}

/** Clears the shared client's socket and timers so tests do not leak into each other. */
function resetSocketClient(): void {
  const client = socketClient as any;
  clearTimeout(client.reconnectTimer);
  clearInterval(client.pingInterval);
  client.reconnectTimer = null;
  client.pingInterval = null;
  client.socket = null;
}

describe('socketClient session-token recovery', () => {
  let tokenFromBackend: string;
  let originalFetch: typeof globalThis.fetch;
  let originalWebSocket: typeof globalThis.WebSocket;

  beforeEach(async () => {
    vi.useFakeTimers();
    resetSocketClient();
    FakeWebSocket.instances = [];
    tokenFromBackend = 'old-token';
    originalFetch = globalThis.fetch;
    originalWebSocket = globalThis.WebSocket;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ token: tokenFromBackend }),
    })) as unknown as typeof globalThis.fetch;
    globalThis.WebSocket = FakeWebSocket as unknown as typeof globalThis.WebSocket;

    // Start every test from an empty token cache.
    const { resetSessionToken } = await import('../common/services/backendApi');
    resetSessionToken();
  });

  afterEach(() => {
    resetSocketClient();
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    globalThis.WebSocket = originalWebSocket;
    vi.restoreAllMocks();
  });

  it('fetches a fresh token after a socket is refused before opening', async () => {
    await socketClient.connect();
    expect(FakeWebSocket.instances[0].url).toContain('token=old-token');

    // The backend restarts with a new token; the refused handshake closes
    // the socket without it ever opening.
    tokenFromBackend = 'new-token';
    FakeWebSocket.instances[0].onclose?.();
    FakeWebSocket.instances[0].readyState = 3;

    await socketClient.connect();
    await flushPromises();
    expect(FakeWebSocket.instances[1].url).toContain('token=new-token');
  });

  it('keeps its token when a socket that did open later closes', async () => {
    await socketClient.connect();
    FakeWebSocket.instances[0].readyState = FakeWebSocket.OPEN;
    FakeWebSocket.instances[0].onopen?.();

    tokenFromBackend = 'new-token';
    FakeWebSocket.instances[0].onclose?.();
    FakeWebSocket.instances[0].readyState = 3;

    await socketClient.connect();
    await flushPromises();
    expect(FakeWebSocket.instances[1].url).toContain('token=old-token');
  });

  it('makes one token request when several sockets ask at the same moment', async () => {
    const { getSessionToken } = await import('../common/services/backendApi');

    const tokens = await Promise.all([getSessionToken(), getSessionToken(), getSessionToken(), getSessionToken()]);

    expect(tokens).toEqual(['old-token', 'old-token', 'old-token', 'old-token']);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });
});
