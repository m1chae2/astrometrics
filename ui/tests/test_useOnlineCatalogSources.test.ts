/**
 * @fileoverview Tests for useOnlineCatalogSources' waiting and cancelling behaviour.
 *
 * The deep-star (Gaia) query takes seconds, and a pan or wheel-zoom changes the
 * view many times a second. These tests pin that an uncached query waits for the
 * view to settle, that a superseded request is cancelled, and that `loading`
 * reports the wait.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

vi.mock('../common/services/backendApi', () => ({
  callBackend: vi.fn(),
}));

import { callBackend } from '../common/services/backendApi';
import { clearCatalogSourceCache } from '../planetariumDisplay/utils/catalogSourceCache';
import {
  useOnlineCatalogSources,
  CATALOG_QUERY_DEBOUNCE_MS,
} from '../planetariumDisplay/hooks/useOnlineCatalogSources';

const mockedCallBackend = vi.mocked(callBackend);

const star = (id: string) => ({ id, ra: 10, dec: 10, name: id, hasSpectra: false, hasPhotometry: false, type: 'star' });

/** Lets already-resolved promises run their continuations. */
const flushPromises = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

describe('useOnlineCatalogSources', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedCallBackend.mockReset();
    clearCatalogSourceCache();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for the view to settle before sending a query', async () => {
    mockedCallBackend.mockResolvedValue([star('a')] as never);
    const { result } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true));

    expect(mockedCallBackend).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(result.current.onlineSources.map(source => source.id)).toEqual(['a']);
    expect(result.current.loading).toBe(false);
  });

  it('sends only one query when the view changes again during the wait', async () => {
    mockedCallBackend.mockResolvedValue([star('b')] as never);
    const { rerender } = renderHook(({ ra }) => useOnlineCatalogSources(ra, 10, 1, ['gaia'], true), {
      initialProps: { ra: 10 },
    });

    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS - 50);
    });
    rerender({ ra: 20 });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(mockedCallBackend.mock.calls[0][1]).toMatchObject({ ra: 20 });
  });

  it('cancels an in-flight request when the view changes, without reporting an error', async () => {
    let firstSignal: AbortSignal | undefined;
    mockedCallBackend.mockImplementationOnce(((_action: string, _params: unknown, options?: { signal?: AbortSignal }) => {
      firstSignal = options?.signal;
      return new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => {
          const abortError = new Error('aborted');
          abortError.name = 'AbortError';
          reject(abortError);
        });
      });
    }) as never);
    mockedCallBackend.mockResolvedValueOnce([star('c')] as never);

    const { result, rerender } = renderHook(({ ra }) => useOnlineCatalogSources(ra, 10, 1, ['gaia'], true), {
      initialProps: { ra: 10 },
    });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    expect(firstSignal?.aborted).toBe(false);

    rerender({ ra: 30 });
    expect(firstSignal?.aborted).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(result.current.error).toBeNull();
    expect(result.current.onlineSources.map(source => source.id)).toEqual(['c']);
    expect(result.current.loading).toBe(false);
  });

  it('skips the wait when told the query never changes with the view', async () => {
    mockedCallBackend.mockResolvedValue([star('d')] as never);
    renderHook(() => useOnlineCatalogSources(0, 0, 180, ['hipparcos'], true, 0));

    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
  });

  it('does not query, and is not loading, when disabled', () => {
    const { result } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], false));

    expect(mockedCallBackend).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });
});
