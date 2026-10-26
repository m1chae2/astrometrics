/**
 * @file catalogQueryPolicy.ts
 * Shared refresh policy for the star-catalog queries.
 *
 * The catalog holds hundreds of thousands of stars, so one list or count
 * request is expensive. These queries therefore never poll on a timer. They
 * refresh when App.tsx invalidates them after a real catalog change, and
 * they do not refetch on remount or window focus while the data is fresh.
 * A failed request throws, so it is never cached as an empty result: the
 * previous data stays and the request is retried with a growing wait.
 */

/** How long catalog data counts as fresh: 5 minutes. */
export const CATALOG_STALE_TIME_MS = 5 * 60 * 1000;

/** Retries after a failed catalog request (for example a "busy, try again" refusal). */
export const CATALOG_RETRY_COUNT = 2;

/**
 * Waits before a retry: 2 s, then 4 s, never more than 10 s.
 *
 * @param attemptIndex Zero for the first retry.
 * @returns Milliseconds to wait.
 */
export const catalogRetryDelay = (attemptIndex: number): number =>
    Math.min(2000 * 2 ** attemptIndex, 10000);

/**
 * Query options shared by every star-catalog query.
 *
 * @param isDisplayActive Whether the display using the query is on screen;
 *     a hidden display does not fetch.
 * @returns Options to spread into `useQuery`.
 */
export const catalogQueryPolicy = (isDisplayActive: boolean) => ({
    staleTime: CATALOG_STALE_TIME_MS,
    refetchOnWindowFocus: false,
    retry: CATALOG_RETRY_COUNT,
    retryDelay: catalogRetryDelay,
    enabled: isDisplayActive,
});
