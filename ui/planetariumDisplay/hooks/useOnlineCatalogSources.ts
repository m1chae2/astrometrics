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
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { findCachedCatalogSources, storeCatalogSources } from '../utils/catalogSourceCache';

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
 * @returns {{ onlineSources: PlanetariumSource[]; loading: boolean; error: string | null }}
 */
export const useOnlineCatalogSources = (
  ra: number,
  dec: number,
  radius: number,
  enabledDrivers: string[],
  enabled: boolean,
) => {
  const [onlineSources, setOnlineSources] = useState<PlanetariumSource[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Use a stable string key for the driver list to avoid identity-change re-fetches
  const enabledDriversKey = enabledDrivers.join(',');

  useEffect(() => {
    if (!enabled || enabledDrivers.length === 0) {
      setOnlineSources([]);
      return;
    }

    const cached = findCachedCatalogSources(ra, dec, radius, enabledDriversKey);
    if (cached) {
      setOnlineSources(cached);
      setError(null);
      setLoading(false);
      return;
    }

    let active = true;

    const fetchOnlineSources = async () => {
      try {
        setLoading(true);
        const data = await callBackend('planetarium:get_catalog_sources', {
          ra,
          dec,
          radius,
          enabled_drivers: enabledDrivers,
        });
        if (active) {
          setOnlineSources(data);
          setError(null);
          storeCatalogSources(ra, dec, radius, enabledDriversKey, data);
        }
      } catch (error: unknown) {
        if (active) {
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

    fetchOnlineSources();
    return () => {
      active = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ra, dec, radius, enabled, enabledDriversKey]);

  return { onlineSources, loading, error };
};
