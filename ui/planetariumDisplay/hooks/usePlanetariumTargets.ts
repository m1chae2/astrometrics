/**
 * @module usePlanetariumTargets
 * @fileoverview React hook for fetching the local library of observation targets.
 *
 */

import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { callBackend } from '../../common/services/backendApi';
import { PlanetariumTarget } from '../../common/types/planetariumTypes';

/**
 * Fetches all observation targets, used for reticle bounds and selection on
 * the Planetarium sky map.
 *
 * Targets are fetched once on mount, and the request is cancelled if the
 * component unmounts before it resolves.
 *
 * @func usePlanetariumTargets
 * @returns {{ targets: PlanetariumTarget[]; loading: boolean; error: string | null }}
 */
export const usePlanetariumTargets = () => {
  const { data, loading, error } = useBackendFetch<PlanetariumTarget[]>(
    (signal) => callBackend('planetarium:get_targets', {}, { signal }),
    [],
    { errorMessage: 'Failed to fetch planetarium targets' }
  );

  return { targets: data ?? [], loading, error };
};
