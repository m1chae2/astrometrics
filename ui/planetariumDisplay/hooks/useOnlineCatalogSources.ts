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

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { findCachedCatalogSources, storeCatalogSources } from '../utils/catalogSourceCache';

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
 * unless a cached prior query already covers the new region. Returns an empty
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
 * @returns {{ onlineSources: PlanetariumSource[]; loading: boolean; error: string | null }}
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

  // Use a stable string key for the driver list to avoid identity-change re-fetches
  const enabledDriversKey = enabledDrivers.join(',');

  useEffect(() => {
    if (!enabled || enabledDrivers.length === 0) {
      setOnlineSources([]);
      setLoading(false);
      return;
    }

    const cached = findCachedCatalogSources(ra, dec, radius, enabledDriversKey, limitingMagnitude);
    if (cached) {
      setOnlineSources(cached);
      setError(null);
      setLoading(false);
      return;
    }

    let active = true;
    const abortController = new AbortController();

    const fetchOnlineSources = async () => {
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
          setOnlineSources(data);
          setError(null);
          storeCatalogSources(ra, dec, radius, enabledDriversKey, data, limitingMagnitude);
        }
      } catch (error: unknown) {
        // A cancelled request means a newer one replaced it; that request owns the state now.
        const wasCancelled = error instanceof Error && error.name === 'AbortError';
        if (active && !wasCancelled) {
          const message = error instanceof Error ? error.message : 'Failed to fetch online catalog sources';
          setError(message);
          setOnlineSources([]);
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
    const debounceTimer = setTimeout(fetchOnlineSources, debounceMilliseconds);
    return () => {
      active = false;
      clearTimeout(debounceTimer);
      abortController.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ra, dec, radius, enabled, enabledDriversKey, limitingMagnitude]);

  return { onlineSources, loading, error };
};
