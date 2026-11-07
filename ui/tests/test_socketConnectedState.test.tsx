/**
 * @fileoverview Tests for the event socket's connected state.
 * They check that the socket announces 'disconnected' when it closes, and that
 * the app context shows the true state, including when it starts after the
 * socket is already open.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { socketClient } from '../common/utils/socketClient';
import { AstrometricsProvider, useAstrometrics } from '../common/context/AstrometricsContext';

vi.mock('../common/services/systemService', () => ({ getSystemConfig: vi.fn(async () => ({})) }));
vi.mock('../common/services/imaging/processingService', () => ({ fetchAllProcesses: vi.fn(async () => []) }));

/** A stand-in WebSocket that lets the test fire its events. */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
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

/** Opens the given fake socket the way the browser would. */
function openFake(fake: FakeWebSocket): void {
  fake.readyState = FakeWebSocket.OPEN;
  fake.onopen?.();
}

/** Closes the given fake socket the way the browser would. */
function closeFake(fake: FakeWebSocket): void {
  fake.readyState = FakeWebSocket.CLOSED;
  fake.onclose?.();
}

/** Clears the shared client's socket, timers and listeners between tests. */
function resetSocketClient(): void {
  const client = socketClient as any;
  clearTimeout(client.reconnectTimer);
  clearInterval(client.pingInterval);
  client.reconnectTimer = null;
  client.pingInterval = null;
  client.socket = null;
  client.announcedConnected = false;
  socketClient.removeAllListeners();
}

describe('socket connected state', () => {
  let originalFetch: typeof globalThis.fetch;
  let originalWebSocket: typeof globalThis.WebSocket;

  beforeEach(async () => {
    vi.useFakeTimers();
    resetSocketClient();
    FakeWebSocket.instances = [];
    originalFetch = globalThis.fetch;
    originalWebSocket = globalThis.WebSocket;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ token: 't' }),
    })) as unknown as typeof globalThis.fetch;
    globalThis.WebSocket = FakeWebSocket as unknown as typeof globalThis.WebSocket;
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

  it('emits disconnected once on close, then connected on reconnect', async () => {
    const events: string[] = [];
    socketClient.on('connected', () => events.push('connected'));
    socketClient.on('disconnected', () => events.push('disconnected'));

    await socketClient.connect();
    openFake(FakeWebSocket.instances[0]);
    closeFake(FakeWebSocket.instances[0]);
    // A repeated close must not announce again.
    FakeWebSocket.instances[0].onclose?.();
    expect(events).toEqual(['connected', 'disconnected']);

    await vi.advanceTimersByTimeAsync(5000);
    openFake(FakeWebSocket.instances[1]);
    expect(events).toEqual(['connected', 'disconnected', 'connected']);
  });

  it('does not emit disconnected when a socket closes without ever opening', async () => {
    const onDisconnected = vi.fn();
    socketClient.on('disconnected', onDisconnected);
    await socketClient.connect();
    closeFake(FakeWebSocket.instances[0]);
    expect(onDisconnected).not.toHaveBeenCalled();
  });

  it('reports connected for a provider mounted while the socket is already open', async () => {
    await socketClient.connect();
    openFake(FakeWebSocket.instances[0]);
    const onConnected = vi.fn();
    socketClient.on('connected', onConnected);

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AstrometricsProvider>{children}</AstrometricsProvider>
    );
    const { result } = renderHook(() => useAstrometrics(), { wrapper });

    expect(result.current.connected).toBe(true);
    expect(onConnected).not.toHaveBeenCalled();
  });

  it('shows false after the socket closes', async () => {
    await socketClient.connect();
    openFake(FakeWebSocket.instances[0]);

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AstrometricsProvider>{children}</AstrometricsProvider>
    );
    const { result } = renderHook(() => useAstrometrics(), { wrapper });
    expect(result.current.connected).toBe(true);

    act(() => closeFake(FakeWebSocket.instances[0]));
    expect(result.current.connected).toBe(false);
  });
});
