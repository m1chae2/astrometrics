/**
 * @module usePlanetariumSources
 * @fileoverview React hook for fetching local library sources near a sky position.
 *
 * Fetches only local database sources (targets and library stellar objects).
 * Online catalog sources (e.g. GAIA, bundled Hipparcos) are handled separately
 * by useOnlineCatalogSources to decouple network latency from this fast local fetch.
 *
 * A request waits for the view to stop changing before it is sent, and is cancelled
 * if the view changes again while it is in flight. A wheel-zoom changes the view
 * dozens of times in a couple of seconds; without this, each step sent its own
 * request and the backend queued them all, so the final view took about ten seconds
 * to arrive after the gesture ended.
 *
 * REQ: PLN-1.1, REQ: PLN-2.1
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { LOCAL_CATALOG_QUERY_DEBOUNCE_MS } from './useOnlineCatalogSources';

/**
 * Fetches local library PlanetariumSource objects within a circular sky region.
 *
 * Re-fetches whenever the center position or radius changes, once the view has been
 * still for LOCAL_CATALOG_QUERY_DEBOUNCE_MS. The radius is typically set to 1.5× the
 * current FOV by the parent component to preload sources at pan edges. The previous
 * sources stay on screen until the new ones arrive.
 *
 * @func usePlanetariumSources
 * @param {number} ra - Query center Right Ascension in degrees.
 * @param {number} dec - Query center Declination in degrees.
 * @param {number} radius - Query radius in degrees.
 * @param {number} limitingMagnitude - Faintest star magnitude the current FOV can display (see
 *   computeLimitingMagnitude). Sent to the backend so it doesn't return, serialize, and ship stars the
 *   renderer would discard anyway -- at a wide FOV the local catalog otherwise matches hundreds of
 *   thousands of them.
 * @param {boolean} includeStarsWithoutCatalogMagnitude - Whether the current FOV is narrow enough to
 *   draw stars that have no catalog magnitude (see UNCATALOGED_STAR_MAX_FOV_DEG). Most local stars are
 *   in this group, so this is what actually thins a wide-FOV query.
 * @returns {{ sources: PlanetariumSource[]; loading: boolean; error: string | null }}
 */
export const usePlanetariumSources = (
  ra: number,
  dec: number,
  radius: number,
  limitingMagnitude: number,
  includeStarsWithoutCatalogMagnitude: boolean,
) => {
  const [sources, setSources] = useState<PlanetariumSource[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const abortController = new AbortController();

    const fetchSources = async () => {
      try {
        const data = await callBackend(
          'planetarium:get_sources',
          {
            ra,
            dec,
            radius,
            limiting_magnitude: limitingMagnitude,
            include_stars_without_catalog_magnitude: includeStarsWithoutCatalogMagnitude,
          },
          { signal: abortController.signal },
        );
        if (active) {
          setSources(data);
          setError(null);
        }
      } catch (error: unknown) {
        // A cancelled request means a newer one replaced it; that request owns the state now.
        const wasCancelled = error instanceof Error && error.name === 'AbortError';
        if (active && !wasCancelled) {
          const message = error instanceof Error ? error.message : 'Failed to fetch planetarium sources';
          setError(message);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    // Show that a request is pending straight away, including during the wait.
    setLoading(true);
    const debounceTimer = setTimeout(fetchSources, LOCAL_CATALOG_QUERY_DEBOUNCE_MS);
    return () => {
      active = false;
      clearTimeout(debounceTimer);
      abortController.abort();
    };
  }, [ra, dec, radius, limitingMagnitude, includeStarsWithoutCatalogMagnitude]);

  return { sources, loading, error };
};
