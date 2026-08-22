/**
 * @module usePlanetariumTargets
 * @fileoverview React hook for fetching the local library of observation targets.
 *
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumTarget } from '../../common/types/planetariumTypes';

/**
 * Fetches all observation targets with associated stacked FITS image metadata.
 *
 * Targets are fetched once on mount. The stacked image paths in each target are
 * consumed by FitsLoaderItem for the FITS overlay pipeline.
 *
 * @func usePlanetariumTargets
 * @returns {{ targets: PlanetariumTarget[]; loading: boolean; error: string | null }}
 */
export const usePlanetariumTargets = () => {
  const [targets, setTargets] = useState<PlanetariumTarget[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const fetchTargets = async () => {
      try {
        setLoading(true);
        const data = await callBackend('planetarium:get_targets', {});
        if (active) {
          setTargets(data);
          setError(null);
        }
      } catch (err: any) {
        if (active) {
          setError(err?.message || 'Failed to fetch planetarium targets');
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    fetchTargets();
    return () => {
      active = false;
    };
  }, []);

  return { targets, loading, error };
};
