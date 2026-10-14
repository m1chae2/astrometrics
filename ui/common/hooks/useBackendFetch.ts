/**
 * @module useBackendFetch
 * @fileoverview Shared hook for the "fetch once, or whenever some inputs
 * change" pattern used across the UI's display hooks.
 *
 * Several hooks (originally in the Planetarium Display) reimplemented this
 * pattern by hand, each slightly differently: a `let active = true` flag to
 * ignore a stale response was common, but the underlying fetch was rarely
 * cancelled, so an in-flight request from a component that has already
 * unmounted (or re-run for new inputs) keeps running until it resolves or
 * times out on its own. This hook centralizes the correct version: an
 * AbortController tied to unmount and to every dependency change, plus
 * consistent loading/error state.
 *
 * This is for the "fetch on mount / on deps change" shape. It does not add
 * caching by id — for that (e.g. switching between many targets or stars and
 * wanting previously loaded ones to reappear instantly) see the pattern in
 * `imageViewerDisplay/hooks/useTargetImage.ts` or `astronomyManager/hooks/useSpectrumData.ts`.
 */

import { useEffect, useRef, useState, type DependencyList } from 'react';

/** Return shape of {@link useBackendFetch}. */
export interface UseBackendFetchResult<T> {
  /** The fetched data, or null before the first successful fetch. */
  data: T | null;
  /** Whether a fetch is currently in flight. */
  loading: boolean;
  /** Error message from the most recent failed fetch, or null. */
  error: string | null;
}

/** Options for {@link useBackendFetch}. */
export interface UseBackendFetchOptions {
  /** When false, skips fetching entirely and leaves `data` at null. Defaults to true. */
  enabled?: boolean;
  /** Fallback error message used when a thrown error has none. */
  errorMessage?: string;
}

/**
 * Fetches data on mount and whenever `deps` changes, aborting the previous
 * request (via the AbortSignal passed to `fetcher`) if the component unmounts
 * or `deps` changes again before it resolves.
 *
 * @param fetcher Called with an AbortSignal that is aborted on cleanup; should
 *   pass it through to `callBackend`'s `signal` option.
 * @param deps Effect dependency list — a new fetch runs whenever this changes.
 * @param options See {@link UseBackendFetchOptions}.
 */
export function useBackendFetch<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: DependencyList,
  options?: UseBackendFetchOptions
): UseBackendFetchResult<T> {
  const { enabled = true, errorMessage = 'Failed to fetch data' } = options ?? {};
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  // Kept in a ref so a fetcher re-created each render (a fresh closure over
  // props) doesn't itself trigger a re-fetch — only `deps` should do that.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let active = true;

    setLoading(true);
    setError(null);

    fetcherRef.current(controller.signal)
      .then((result) => {
        if (active) {
          setData(result);
        }
      })
      .catch((err: unknown) => {
        const wasCancelled = err instanceof Error && err.name === 'AbortError';
        if (active && !wasCancelled) {
          setError(err instanceof Error ? err.message : errorMessage);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error };
}
