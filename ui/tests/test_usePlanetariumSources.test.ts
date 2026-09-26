/**
 * @fileoverview Tests for usePlanetariumSources' waiting and cancelling behaviour.
 *
 * A wheel-zoom changes the view dozens of times in a couple of seconds. These
 * tests pin that the library-sources request waits for the view to settle, that
 * a superseded request is cancelled, and that a cancelled request never shows
 * an error or overwrites newer sources.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

vi.mock('../common/services/backendApi', () => ({
  callBackend: vi.fn(),
}));

import { callBackend } from '../common/services/backendApi';
import { usePlanetariumSources } from '../planetariumDisplay/hooks/usePlanetariumSources';
import { LOCAL_CATALOG_QUERY_DEBOUNCE_MS } from '../planetariumDisplay/hooks/useOnlineCatalogSources';

const mockedCallBackend = vi.mocked(callBackend);

const star = (id: string) => ({ id, ra: 10, dec: 10, name: id, hasSpectra: false, hasPhotometry: false, type: 'star' });

/** Lets already-resolved promises run their continuations. */
const flushPromises = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

describe('usePlanetariumSources', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedCallBackend.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for the view to settle before sending a request', async () => {
    mockedCallBackend.mockResolvedValue([star('a')] as never);
    const { result } = renderHook(() => usePlanetariumSources(10, 10, 5, 12, true));

    expect(mockedCallBackend).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(result.current.sources.map(source => source.id)).toEqual(['a']);
    expect(result.current.loading).toBe(false);
  });

  it('sends one request for a whole burst of view changes, using the last view', async () => {
    mockedCallBackend.mockResolvedValue([star('b')] as never);
    const { rerender } = renderHook(({ radius }) => usePlanetariumSources(10, 10, radius, 12, true), {
      initialProps: { radius: 50 },
    });

    // 12 zoom steps, each arriving before the previous wait has finished.
    for (let step = 1; step <= 12; step++) {
      await act(async () => {
        vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS / 4);
      });
      rerender({ radius: 50 - step * 3 });
    }
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(mockedCallBackend.mock.calls[0][1]).toMatchObject({ radius: 14 });
  });

  it('cancels a request in flight when the view changes again', async () => {
    let firstSignal: AbortSignal | undefined;
    mockedCallBackend.mockImplementationOnce(((_action: string, _params: unknown, options?: { signal?: AbortSignal }) => {
      firstSignal = options?.signal;
      return new Promise(() => {});
    }) as never);
    mockedCallBackend.mockResolvedValue([star('newer')] as never);
    const { rerender } = renderHook(({ radius }) => usePlanetariumSources(10, 10, radius, 12, true), {
      initialProps: { radius: 5 },
    });
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    expect(firstSignal?.aborted).toBe(false);

    rerender({ radius: 4 });

    expect(firstSignal?.aborted).toBe(true);
  });

  it('does not report an error or replace newer sources when a request is cancelled', async () => {
    const cancelledError = Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
    mockedCallBackend.mockRejectedValueOnce(cancelledError);
    mockedCallBackend.mockResolvedValue([star('newer')] as never);
    const { result, rerender } = renderHook(({ radius }) => usePlanetariumSources(10, 10, radius, 12, true), {
      initialProps: { radius: 5 },
    });
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();
    expect(result.current.error).toBeNull();

    rerender({ radius: 4 });
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(result.current.error).toBeNull();
    expect(result.current.sources.map(source => source.id)).toEqual(['newer']);
  });

  it('keeps the previous sources on screen while a new request is pending', async () => {
    mockedCallBackend.mockResolvedValueOnce([star('old')] as never);
    mockedCallBackend.mockReturnValueOnce(new Promise(() => {}) as never);
    const { result, rerender } = renderHook(({ radius }) => usePlanetariumSources(10, 10, radius, 12, true), {
      initialProps: { radius: 5 },
    });
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    rerender({ radius: 4 });
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });

    expect(result.current.sources.map(source => source.id)).toEqual(['old']);
    expect(result.current.loading).toBe(true);
  });

  it('reports a real failure as an error', async () => {
    mockedCallBackend.mockRejectedValue(new Error('backend down'));
    const { result } = renderHook(() => usePlanetariumSources(10, 10, 5, 12, true));

    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(result.current.error).toBe('backend down');
    expect(result.current.loading).toBe(false);
  });

  it('passes extended timeout and silent options to callBackend to tolerate backend load', async () => {
    mockedCallBackend.mockResolvedValue([star('load_test')] as never);
    renderHook(() => usePlanetariumSources(10, 10, 5, 12, true));

    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(mockedCallBackend).toHaveBeenCalledTimes(1);
    expect(mockedCallBackend.mock.calls[0][2]).toMatchObject({
      timeoutMs: 30000,
      silent: true,
    });
  });

  it('preserves existing sources when a subsequent request fails with a timeout', async () => {
    mockedCallBackend.mockResolvedValueOnce([star('initial')] as never);
    mockedCallBackend.mockRejectedValueOnce(new Error('Request timed out for planetarium:get_sources'));
    const { result, rerender } = renderHook(({ radius }) => usePlanetariumSources(10, 10, radius, 12, true), {
      initialProps: { radius: 5 },
    });

    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(result.current.sources.map(s => s.id)).toEqual(['initial']);

    rerender({ radius: 8 });
    await act(async () => {
      vi.advanceTimersByTime(LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    });
    await flushPromises();

    expect(result.current.sources.map(s => s.id)).toEqual(['initial']);
    expect(result.current.error).toBe('Request timed out for planetarium:get_sources');
    expect(result.current.loading).toBe(false);
  });
});
