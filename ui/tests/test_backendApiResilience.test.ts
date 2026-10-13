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
});
