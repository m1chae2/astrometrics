/**
 * @module usePlanetariumSources
 * @fileoverview React hook for fetching local library sources near a sky position.
 *
 * Fetches only local database sources (targets and library stellar objects).
 * Online catalog sources (e.g. GAIA, bundled Hipparcos) are handled separately
 * by useOnlineCatalogSources to decouple network latency from this fast local fetch.
 *
 * REQ: PLN-1.1, REQ: PLN-2.1
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumSource } from '../../common/types/planetariumTypes';

/**
 * Fetches local library PlanetariumSource objects within a circular sky region.
 *
 * Re-fetches whenever the center position or radius changes. The radius is typically
 * set to 1.5× the current FOV by the parent component to preload sources at pan edges.
 *
 * @func usePlanetariumSources
 * @param {number} ra - Query center Right Ascension in degrees.
 * @param {number} dec - Query center Declination in degrees.
 * @param {number} radius - Query radius in degrees.
 * @returns {{ sources: PlanetariumSource[]; loading: boolean; error: string | null }}
 */
export const usePlanetariumSources = (
  ra: number,
  dec: number,
  radius: number,
) => {
  const [sources, setSources] = useState<PlanetariumSource[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const fetchSources = async () => {
      try {
        setLoading(true);
        const data = await callBackend('planetarium:get_sources', { ra, dec, radius });
        if (active) {
          setSources(data);
          setError(null);
        }
      } catch (error: unknown) {
        if (active) {
          const message = error instanceof Error ? error.message : 'Failed to fetch planetarium sources';
          setError(message);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    fetchSources();
    return () => {
      active = false;
    };
  }, [ra, dec, radius]);

  return { sources, loading, error };
};
