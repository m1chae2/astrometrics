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
  CATALOG_RETRY_DELAYS_MS,
} from '../planetariumDisplay/hooks/useOnlineCatalogSources';

const mockedCallBackend = vi.mocked(callBackend);

const star = (id: string) => ({ id, ra: 10, dec: 10, name: id, hasSpectra: false, hasPhotometry: false, type: 'star' });

/** Wraps stars the way the backend replies. */
const reply = (sources: ReturnType<typeof star>[], starsTruncated: boolean, failedCatalogs: string[] = []) => ({
  sources,
  starsTruncated,
  failedCatalogs,
});

/** A reply the backend did not cut short. */
const complete = (sources: ReturnType<typeof star>[]) => reply(sources, false);

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
    mockedCallBackend.mockResolvedValue(complete([star('a')]) as never);
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
    mockedCallBackend.mockResolvedValue(complete([star('b')]) as never);
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
    mockedCallBackend.mockResolvedValueOnce(complete([star('c')]) as never);

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
    mockedCallBackend.mockResolvedValue(complete([star('d')]) as never);
    renderHook(() => useOnlineCatalogSources(0, 0, 180, ['hipparcos'], true, { debounceMilliseconds: 0 }));

    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
  });

  it('sends the limiting magnitude to the backend', async () => {
    mockedCallBackend.mockResolvedValue(complete([star('e')]) as never);
    renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true, { limitingMagnitude: 14 }));

    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend.mock.calls[0][1]).toMatchObject({ limiting_magnitude: 14 });
  });

  it('refetches when the view needs stars fainter than the cached region holds', async () => {
    mockedCallBackend.mockResolvedValue(complete([star('f')]) as never);
    const { rerender } = renderHook(
      ({ limit }) => useOnlineCatalogSources(10, 10, 1, ['gaia'], true, { limitingMagnitude: limit }),
      { initialProps: { limit: 12 } },
    );
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();
    expect(mockedCallBackend).toHaveBeenCalledTimes(1);

    // A shallower need is already covered: no new query.
    rerender({ limit: 10 });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();
    expect(mockedCallBackend).toHaveBeenCalledTimes(1);

    // A deeper need is not: the cached region lacks the fainter stars.
    rerender({ limit: 15 });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
  });

  it('does not query, and is not loading, when disabled', () => {
    const { result } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], false));

    expect(mockedCallBackend).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it('sends a new request for a zoom-in after a result the backend cut short', async () => {
    mockedCallBackend.mockResolvedValue(reply([star('g')], true) as never);
    const { rerender } = renderHook(({ radius }) => useOnlineCatalogSources(10, 10, radius, ['gaia'], true, { limitingMagnitude: 14 }), {
      initialProps: { radius: 4 },
    });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();
    expect(mockedCallBackend).toHaveBeenCalledTimes(1);

    rerender({ radius: 2 });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
    expect(mockedCallBackend.mock.calls[1][1]).toMatchObject({ radius: 2 });
  });

  it('sends no request for a zoom-in after a complete result', async () => {
    mockedCallBackend.mockResolvedValue(complete([star('h')]) as never);
    const { rerender } = renderHook(({ radius }) => useOnlineCatalogSources(10, 10, radius, ['gaia'], true, { limitingMagnitude: 14 }), {
      initialProps: { radius: 4 },
    });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    rerender({ radius: 2 });
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
  });

  /** Runs the first query's wait so the first request is sent. */
  const sendFirstRequest = async () => {
    await act(async () => {
      vi.advanceTimersByTime(CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();
  };

  /** Moves the clock forward and lets the retry's promise settle. */
  const advance = async (milliseconds: number) => {
    await act(async () => {
      vi.advanceTimersByTime(milliseconds);
    });
    await flushPromises();
  };

  it('retries a failed request and shows its stars', async () => {
    mockedCallBackend.mockRejectedValueOnce(new Error('backend starting') as never);
    mockedCallBackend.mockResolvedValueOnce(complete([star('r')]) as never);
    const { result } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true));
    await sendFirstRequest();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe('backend starting');

    await advance(CATALOG_RETRY_DELAYS_MS[0]);

    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
    expect(result.current.onlineSources.map(source => source.id)).toEqual(['r']);
    expect(result.current.error).toBeNull();

    // A success ends the retries.
    await advance(CATALOG_RETRY_DELAYS_MS[3] * 2);
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
  });

  it('shows the stars of a reply with failed catalogs, retries, and does not cache it', async () => {
    mockedCallBackend.mockResolvedValueOnce(reply([star('p')], false, ['deep_stars']) as never);
    mockedCallBackend.mockResolvedValueOnce(complete([star('p'), star('q')]) as never);
    const { result } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true));
    await sendFirstRequest();

    expect(result.current.onlineSources.map(source => source.id)).toEqual(['p']);
    expect(result.current.failedCatalogs).toEqual(['deep_stars']);
    expect(result.current.error).toBeNull();

    await advance(CATALOG_RETRY_DELAYS_MS[0]);

    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
    expect(result.current.onlineSources.map(source => source.id)).toEqual(['p', 'q']);
    expect(result.current.failedCatalogs).toEqual([]);

    // The complete reply ends the retries.
    await advance(CATALOG_RETRY_DELAYS_MS[3] * 2);
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
  });

  it('does not cache or reuse a reply with failed catalogs', async () => {
    mockedCallBackend.mockResolvedValue(reply([star('p')], false, ['deep_stars']) as never);
    const { rerender } = renderHook(({ radius }) => useOnlineCatalogSources(10, 10, radius, ['gaia'], true), {
      initialProps: { radius: 1 },
    });
    await sendFirstRequest();
    expect(mockedCallBackend).toHaveBeenCalledTimes(1);

    // A zoom-in inside the failed view must send its own request.
    rerender({ radius: 0.5 });
    await sendFirstRequest();
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
  });

  it('waits longer between retries while the request keeps failing', async () => {
    mockedCallBackend.mockRejectedValue(new Error('down') as never);
    renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true));
    await sendFirstRequest();

    await advance(CATALOG_RETRY_DELAYS_MS[0] - 1);
    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);

    await advance(CATALOG_RETRY_DELAYS_MS[1]);
    expect(mockedCallBackend).toHaveBeenCalledTimes(3);
  });

  it('does not retry a cancelled request', async () => {
    mockedCallBackend.mockImplementation((() => {
      const abortError = new Error('aborted');
      abortError.name = 'AbortError';
      return Promise.reject(abortError);
    }) as never);
    const { result } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true));
    await sendFirstRequest();
    await advance(CATALOG_RETRY_DELAYS_MS[3] * 2);

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  it('cancels a pending retry on unmount', async () => {
    mockedCallBackend.mockRejectedValue(new Error('down') as never);
    const { unmount } = renderHook(() => useOnlineCatalogSources(10, 10, 1, ['gaia'], true));
    await sendFirstRequest();
    unmount();
    await advance(CATALOG_RETRY_DELAYS_MS[3] * 2);

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
  });

  it('cancels a pending retry when the inputs change', async () => {
    mockedCallBackend.mockRejectedValueOnce(new Error('down') as never);
    mockedCallBackend.mockResolvedValue(complete([star('n')]) as never);
    const { rerender } = renderHook(({ ra }) => useOnlineCatalogSources(ra, 10, 1, ['gaia'], true), {
      initialProps: { ra: 10 },
    });
    await sendFirstRequest();

    rerender({ ra: 50 });
    await advance(CATALOG_QUERY_DEBOUNCE_MS);
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
    expect(mockedCallBackend.mock.calls[1][1]).toMatchObject({ ra: 50 });

    // The old query's retry never fires.
    await advance(CATALOG_RETRY_DELAYS_MS[3] * 2);
    expect(mockedCallBackend).toHaveBeenCalledTimes(2);
  });
});
