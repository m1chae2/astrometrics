/**
 * @fileoverview Unit tests for BackendManager.waitUntilWarm.
 *
 * The desktop shell holds its splash screen until the backend reports that its
 * star catalog is loaded, so a slow or broken backend must never be able to
 * keep the app from opening: every path here either returns true (ready) or
 * false (gave up), and none of them hangs.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('electron-log', () => ({ default: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));

import { BackendManager } from '../../electron/backend_manager.js';

const FAST_POLL = { pollIntervalMs: 1 };

const readyResponse = (status: number) => ({ ok: status >= 200 && status < 300, status });

describe('BackendManager.waitUntilWarm', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('keeps polling while the backend answers 503, then returns true once it is ready', async () => {
    fetchMock
      .mockResolvedValueOnce(readyResponse(503))
      .mockResolvedValueOnce(readyResponse(503))
      .mockResolvedValueOnce(readyResponse(200));

    const manager = new BackendManager({});
    expect(await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('keeps polling through connection errors while the backend is still starting', async () => {
    fetchMock
      .mockRejectedValueOnce(new Error('ECONNREFUSED'))
      .mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValueOnce(readyResponse(200));

    const manager = new BackendManager({});
    expect(await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('does not wait on a backend that has no /api/ready route', async () => {
    fetchMock.mockResolvedValue(readyResponse(404));

    const manager = new BackendManager({});
    expect(await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('gives up after the timeout so the app still opens', async () => {
    fetchMock.mockResolvedValue(readyResponse(503));

    const manager = new BackendManager({});
    expect(await manager.waitUntilWarm({ timeoutMs: 30, ...FAST_POLL })).toBe(false);
  });

  it('stops immediately when the spawned backend has already exited', async () => {
    fetchMock.mockResolvedValue(readyResponse(503));

    const manager = new BackendManager({});
    manager.backendExited = true;
    expect(await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL })).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('polls the configured backend port', async () => {
    vi.stubEnv('ASTROMETRICS_PORT', '5123');
    fetchMock.mockResolvedValue(readyResponse(200));

    const manager = new BackendManager({});
    await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL });
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:5123/api/ready');
  });

  it('gives up early and reports "unreachable" when nothing ever answers', async () => {
    fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));

    const manager = new BackendManager({});
    expect(await manager.waitUntilWarm({ timeoutMs: 5000, unreachableTimeoutMs: 20, ...FAST_POLL })).toBe(false);
    expect(manager.warmUpFailureReason).toBe('unreachable');
  });

  it('reports "timeout" when the backend answers but never becomes ready', async () => {
    fetchMock.mockResolvedValue(readyResponse(503));

    const manager = new BackendManager({});
    await manager.waitUntilWarm({ timeoutMs: 30, ...FAST_POLL });
    expect(manager.warmUpFailureReason).toBe('timeout');
  });

  it('reports "exited" and clears the reason again on a later successful wait', async () => {
    const manager = new BackendManager({});
    manager.backendExited = true;
    await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL });
    expect(manager.warmUpFailureReason).toBe('exited');

    manager.backendExited = false;
    fetchMock.mockResolvedValue(readyResponse(200));
    expect(await manager.waitUntilWarm({ timeoutMs: 5000, ...FAST_POLL })).toBe(true);
    expect(manager.warmUpFailureReason).toBeNull();
  });

  it('stop() terminates spawned backend process and cleans up any run pids', () => {
    const mockTerminate = vi.fn();
    const mockPlatform = { terminateProcessTree: mockTerminate };
    const manager = new BackendManager({}, mockPlatform as any);
    manager.backendProcess = { killed: false, pid: 1234 } as any;

    const cleanupSpy = vi.spyOn(manager as any, '_cleanupOrphanedRunPids');
    manager.stop();

    expect(mockTerminate).toHaveBeenCalledWith(manager.backendProcess);
    expect(cleanupSpy).toHaveBeenCalled();
  });
});
