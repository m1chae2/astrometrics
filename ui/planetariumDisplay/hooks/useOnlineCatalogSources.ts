/**
 * @module useOnlineCatalogSources
 * @fileoverview React hook for fetching online catalog sources (e.g. bundled
 * Hipparcos, GAIA DR3) independently of the local source fetch.
 *
 * Decoupled from usePlanetariumSources so that network latency from online
 * catalog queries never delays the initial local source load.
 *
 * Query results are cached (see catalogSourceCache) and reused instantly,
 * without a backend round trip, whenever a previous query's region already
 * covers the new one — e.g. zooming in and back out no longer waits on a
 * fresh fetch for a region that was just loaded a moment ago.
 *
 * A query that is not cached waits for the view to stop changing before it is
 * sent, and is cancelled if the view changes again while it is in flight, so a
 * fast pan or wheel-zoom sends one request instead of one per intermediate view.
 */

import { useState, useEffect, useRef } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { findCachedCatalogSources, storeCatalogSources } from '../utils/catalogSourceCache';
import { isCoveredByFetched, sameSources, SourceQueryShape } from '../utils/sourceRequestRule';

/**
 * How long the view must stay unchanged, in milliseconds, before an uncached
 * catalog query is sent.
 *
 * A wheel-zoom or drag changes the view many times a second, and one deep-star
 * query takes seconds, so sending a query per step would queue up requests for
 * views that are already gone. This value was chosen by judgement, not
 * measured: long enough to skip the intermediate steps of one gesture, short
 * enough that the wait after the gesture ends is barely noticeable.
 */
export const CATALOG_QUERY_DEBOUNCE_MS = 300;

/**
 * The wait, in milliseconds, for a catalog that is read from this computer.
 *
 * A local lookup takes tens of milliseconds (about 50 ms for a busy view on a synthetic
 * 10 million star catalog), so it does not need the 300 ms a remote query does. A short
 * wait still skips the intermediate views of one wheel-zoom or drag, which would
 * otherwise each cost a lookup and a large response. It is longer than the 200 ms rhythm at
 * which a zoom gesture reports its field of view (FOV_NOTIFY_INTERVAL_MS), so no lookup starts
 * mid-gesture. Chosen by judgement, not measured.
 */
export const LOCAL_CATALOG_QUERY_DEBOUNCE_MS = 250;

/**
 * The waits, in milliseconds, before each retry of a failed catalog request.
 *
 * The first retries come quickly because the usual cause is a backend that is still
 * starting. The last value repeats, so a backend that stays down is asked only twice a
 * minute. Chosen by judgement, not measured.
 */
export const CATALOG_RETRY_DELAYS_MS = [2000, 5000, 10000, 30000];

/**
 * Gives the wait before a retry.
 *
 * @func retryDelayMilliseconds
 * @param {number} failureCount - How many requests of this query have failed so far (1 or more).
 * @returns {number} The wait in milliseconds; the last list value once the list runs out.
 */
const retryDelayMilliseconds = (failureCount: number): number =>
  CATALOG_RETRY_DELAYS_MS[Math.min(failureCount, CATALOG_RETRY_DELAYS_MS.length) - 1];

/**
 * Tells whether two lists of catalog names hold the same names in the same order.
 *
 * @func sameNames
 * @param {string[]} first - One list.
 * @param {string[]} second - The other list.
 * @returns {boolean} True when the lists are equal.
 */
const sameNames = (first: string[], second: string[]): boolean =>
  first.length === second.length && first.every((name, index) => name === second[index]);

/**
 * Optional settings for useOnlineCatalogSources.
 */
export interface UseOnlineCatalogSourcesOptions {
  /**
   * How long the view must stay unchanged, in milliseconds, before an uncached query is sent.
   * Defaults to CATALOG_QUERY_DEBOUNCE_MS; pass 0 for a query whose arguments never change with
   * the view (e.g. the fixed whole-sky Hipparcos query).
   */
  debounceMilliseconds?: number;
  /**
   * Faintest magnitude worth fetching. Sent to the backend so drivers that can use it (Gaia)
   * fetch fewer stars, and used to decide whether a cached region is deep enough to reuse.
   * Omit for no limit.
   */
  limitingMagnitude?: number;
}

/**
 * Fetches online catalog sources for enabled drivers within a sky region.
 *
 * Re-fetches whenever the center position, radius, or enabled driver list changes,
 * unless the sources on screen or a cached prior query already cover the new region.
 * A zoom-in that stays inside the sources on screen changes nothing, but only when those
 * sources are complete: a result the backend cut short (Gaia stops at 20,000 stars) covers no
 * other view, so the next view sends its own request. A request that fails (not one that is
 * cancelled) is quietly sent again after a growing wait (CATALOG_RETRY_DELAYS_MS) until it
 * succeeds. A reply that names failed catalogs counts as a failed request: its stars are shown, but
 * it is not cached and covers no other view, and the same retry follows. The retries continue until it
 * succeeds, the inputs change, or the component unmounts. Returns an empty
 * result set immediately when no drivers are enabled, without issuing a backend
 * request.
 *
 * @func useOnlineCatalogSources
 * @param {number} ra - Query center Right Ascension in degrees.
 * @param {number} dec - Query center Declination in degrees.
 * @param {number} radius - Query radius in degrees.
 * @param {string[]} enabledDrivers - Registry keys of drivers to query (e.g. ['hipparcos', 'gaia']).
 * @param {boolean} enabled - When false, returns empty results without querying.
 * @param {UseOnlineCatalogSourcesOptions} options - Optional tuning; see UseOnlineCatalogSourcesOptions.
 * @returns {{ onlineSources: PlanetariumSource[]; loading: boolean; error: string | null; failedCatalogs: string[] }}
 *   failedCatalogs names the drivers that could not answer the latest request (empty when all did).
 */
export const useOnlineCatalogSources = (
  ra: number,
  dec: number,
  radius: number,
  enabledDrivers: string[],
  enabled: boolean,
  options: UseOnlineCatalogSourcesOptions = {},
) => {
  const { debounceMilliseconds = CATALOG_QUERY_DEBOUNCE_MS, limitingMagnitude } = options;
  const [onlineSources, setOnlineSources] = useState<PlanetariumSource[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [failedCatalogs, setFailedCatalogs] = useState<string[]>([]);

  // Use a stable string key for the driver list to avoid identity-change re-fetches
  const enabledDriversKey = enabledDrivers.join(',');

  // The query whose sources are on screen. A smaller, shallower query inside it (zooming in)
  // needs no request and no new array: the renderer already drops what is out of view.
  const appliedQueryRef = useRef<(SourceQueryShape & { driversKey: string }) | null>(null);

  useEffect(() => {
    if (!enabled || enabledDrivers.length === 0) {
      appliedQueryRef.current = null;
      setOnlineSources(previous => (previous.length === 0 ? previous : []));
      setFailedCatalogs(previous => (previous.length === 0 ? previous : []));
      setLoading(false);
      return;
    }

    const wantedQuery: SourceQueryShape = { ra, dec, radius, limitingMagnitude };
    const applied = appliedQueryRef.current;
    if (applied && applied.driversKey === enabledDriversKey && isCoveredByFetched(applied, wantedQuery)) {
      setLoading(false);
      return;
    }

    const cached = findCachedCatalogSources(ra, dec, radius, enabledDriversKey, limitingMagnitude);
    if (cached) {
      // The cache never returns a truncated entry, so this result is complete.
      appliedQueryRef.current = { ...wantedQuery, driversKey: enabledDriversKey, truncated: false };
      setOnlineSources(previous => (sameSources(previous, cached) ? previous : cached));
      setError(null);
      setFailedCatalogs(previous => (previous.length === 0 ? previous : []));
      setLoading(false);
      return;
    }

    let active = true;
    const abortController = new AbortController();

    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    /**
     * Sends the query once; on failure, schedules the next try.
     *
     * @param {number} failureCount - How many earlier requests of this query have failed.
     */
    const fetchOnlineSources = async (failureCount: number) => {
      try {
        const data = await callBackend(
          'planetarium:get_catalog_sources',
          {
            ra,
            dec,
            radius,
            enabled_drivers: enabledDrivers,
            limiting_magnitude: limitingMagnitude,
          },
          { signal: abortController.signal },
        );
        if (active) {
          const { sources: fetchedSources, starsTruncated, failedCatalogs = [] } = data;
          const catalogFailed = failedCatalogs.length > 0;
          // A truncated result is shown, but covers no other view (see isCoveredByFetched).
          // A result missing a failed catalog's stars is treated the same way.
          appliedQueryRef.current = {
            ...wantedQuery,
            driversKey: enabledDriversKey,
            truncated: starsTruncated || catalogFailed,
          };
          setOnlineSources(previous => (sameSources(previous, fetchedSources) ? previous : fetchedSources));
          setError(null);
          setFailedCatalogs(previous => (sameNames(previous, failedCatalogs) ? previous : failedCatalogs));
          if (catalogFailed) {
            // Quiet on purpose: an unreadable catalog is a status. Not cached, so ask again later.
            retryTimer = setTimeout(
              () => fetchOnlineSources(failureCount + 1),
              retryDelayMilliseconds(failureCount + 1),
            );
          } else {
            storeCatalogSources(ra, dec, radius, enabledDriversKey, fetchedSources, limitingMagnitude, starsTruncated);
          }
        }
      } catch (error: unknown) {
        // A cancelled request means a newer one replaced it; that request owns the state now.
        const wasCancelled = error instanceof Error && error.name === 'AbortError';
        if (active && !wasCancelled) {
          const message = error instanceof Error ? error.message : 'Failed to fetch online catalog sources';
          setError(message);
          appliedQueryRef.current = null;
          setOnlineSources([]);
          // Quiet on purpose: an unreachable backend is a status, so no toast or console error.
          retryTimer = setTimeout(() => fetchOnlineSources(failureCount + 1), retryDelayMilliseconds(failureCount + 1));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    // Show that a query is pending straight away, including during the debounce
    // wait. The previous sources stay on screen until the new ones arrive.
    setLoading(true);
    const debounceTimer = setTimeout(() => fetchOnlineSources(0), debounceMilliseconds);
    return () => {
      active = false;
      clearTimeout(debounceTimer);
      clearTimeout(retryTimer);
      abortController.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ra, dec, radius, enabled, enabledDriversKey, limitingMagnitude]);

  return { onlineSources, loading, error, failedCatalogs };
};
