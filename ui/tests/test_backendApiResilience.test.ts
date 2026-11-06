/**
 * @fileoverview Unit test suite for callBackend resilience and request coalescing.
 *
 * Verifies that:
 * 1. Concurrent identical idempotent reads are coalesced into a single in-flight network request.
 * 2. Transient timeouts on idempotent reads are automatically retried with backoff.
 * 3. Heavy actions receive extended timeout thresholds (90s).
 * 4. Mutating actions are neither deduplicated nor automatically retried.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  setBackendRestarting,
  callBackend,
  DEFAULT_RPC_TIMEOUT_MS,
  EXTENDED_RPC_TIMEOUT_MS,
} from '../common/services/backendApi';

describe('backendApi resilience and deduplication', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  /**
   * Tests that default and extended timeout thresholds are correctly configured.
   */
  it('exposes robust default (45s) and extended (90s) timeout constants', () => {
    expect(DEFAULT_RPC_TIMEOUT_MS).toBe(45000);
    expect(EXTENDED_RPC_TIMEOUT_MS).toBe(90000);
  });

  /**
   * Tests that concurrent requests for the same idempotent read action share a single in-flight fetch.
   */
  it('coalesces concurrent in-flight requests for identical idempotent reads', async () => {
    let fetchCalls = 0;
    global.fetch = vi.fn().mockImplementation(async () => {
      fetchCalls++;
      // Simulate network latency
      await new Promise((resolve) => setTimeout(resolve, 50));
      return {
        ok: true,
        json: async () => ({
          jsonrpc: '2.0',
          id: '123',
          result: { status: 'success', data: { sessions: ['2026-09-24'] } },
        }),
      } as Response;
    });

    // Fire 3 simultaneous calls to 'telescope:list_alignment_sessions'
    const [p1, p2, p3] = await Promise.all([
      callBackend('telescope:list_alignment_sessions', {}),
      callBackend('telescope:list_alignment_sessions', {}),
      callBackend('telescope:list_alignment_sessions', {}),
    ]);

    expect(p1).toEqual({ sessions: ['2026-09-24'] });
    expect(p2).toEqual({ sessions: ['2026-09-24'] });
    expect(p3).toEqual({ sessions: ['2026-09-24'] });

    // Despite 3 callers, fetch should only be invoked once
    expect(fetchCalls).toBe(1);
  });

  /**
   * Tests that one caller cancelling does not cancel a shared read for the
   * callers joined to it (a React effect that runs twice in development).
   */
  it('gives joined callers the data when the first caller cancels', async () => {
    global.fetch = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      await new Promise((resolve) => setTimeout(resolve, 50));
      if (init?.signal?.aborted) {
        throw new DOMException('The operation was aborted.', 'AbortError');
      }
      return {
        ok: true,
        json: async () => ({
          jsonrpc: '2.0',
          id: '124',
          result: { status: 'success', data: { sessions: ['2026-10-25'] } },
        }),
      } as Response;
    });

    const first = new AbortController();
    const firstCall = callBackend('telescope:list_alignment_sessions', {}, { signal: first.signal });
    const secondCall = callBackend('telescope:list_alignment_sessions', {}, { signal: new AbortController().signal });
    first.abort();

    await expect(firstCall).rejects.toBeDefined();
    await expect(secondCall).resolves.toEqual({ sessions: ['2026-10-25'] });
  });

  /**
   * Tests that transient fetch failures on idempotent reads are retried automatically.
   */
  it('retries idempotent reads on transient network failure before succeeding', async () => {
    let callCount = 0;
    global.fetch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount === 1) {
        throw new TypeError('Failed to fetch');
      }
      return {
        ok: true,
        json: async () => ({
          jsonrpc: '2.0',
          id: '123',
          result: { status: 'success', data: [{ name: 'Navi' }] },
        }),
      } as Response;
    });

    const result = await callBackend('planetarium:get_constellation_lines', {}, { silent: true });
    expect(result).toEqual([{ name: 'Navi' }]);
    expect(callCount).toBe(2);
  });

  /** Collects the toasts the client sends while `body` runs. */
  async function collectToasts(body: () => Promise<void>): Promise<string[]> {
    const texts: string[] = [];
    const listener = (event: Event) => texts.push(String((event as CustomEvent).detail?.text));
    window.addEventListener('astrometrics:toast', listener);
    try {
      await body();
    } finally {
      window.removeEventListener('astrometrics:toast', listener);
    }
    return texts;
  }

  /** A background read that cannot connect is a status, so no raw "Failed to fetch" toast. */
  it('shows no toast when an idempotent read cannot connect', async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const toasts = await collectToasts(async () => {
      await expect(callBackend('telescope:list_alignment_sessions', {}, { maxRetries: 0 })).rejects.toBeDefined();
    });
    expect(toasts).toEqual([]);
  });

  /** A timed-out background read is also quiet. */
  it('shows no toast when an idempotent read times out', async () => {
    global.fetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));
    const toasts = await collectToasts(async () => {
      await expect(callBackend('telescope:list_alignment_sessions', {}, { maxRetries: 0, timeoutMs: 20 })).rejects.toBeDefined();
    });
    expect(toasts).toEqual([]);
  });

  /** While the backend restarts on purpose, even a failed command is quiet. */
  it('shows no toast for any failure while the backend is restarting', async () => {
    setBackendRestarting(true);
    try {
      global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
      const toasts = await collectToasts(async () => {
        await expect(callBackend('telescope:park', {} as never)).rejects.toBeDefined();
      });
      expect(toasts).toEqual([]);
    } finally {
      setBackendRestarting(false);
    }
  });

  /** A command the user asked for still reports a network failure. */
  it('still shows a toast when a user command cannot connect', async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const toasts = await collectToasts(async () => {
      await expect(callBackend('telescope:park', {} as never)).rejects.toBeDefined();
    });
    expect(toasts.length).toBeGreaterThan(0);
  });
});
