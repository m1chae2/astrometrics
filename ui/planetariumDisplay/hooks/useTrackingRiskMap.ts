/**
 * @module useTrackingRiskMap
 * @fileoverview Fetches the sky's tracking-risk grid for the planetarium's
 * tracking-risk overlay. The backend works the grid out (it is part of the
 * equipment's performance envelope); this hook only asks for it when the
 * overlay is turned on.
 */

import { useEffect, useState } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { TrackingRiskMap } from '../../common/types/backendTypes';

/** Return shape of useTrackingRiskMap. */
export interface TrackingRiskMapState {
  /** The grid, or null before it loads or when no telescope and camera are active. */
  trackingRisk: TrackingRiskMap | null;
  /** True once a reply has come back (even an empty one). */
  loaded: boolean;
}

/**
 * Loads the tracking-risk grid the first time the overlay is shown, and
 * again each time it is turned back on, so new plate solves are included.
 *
 * @func useTrackingRiskMap
 * @param {boolean} enabled - Whether the tracking-risk overlay is showing.
 * @returns {TrackingRiskMapState} The grid and whether it has loaded.
 */
export const useTrackingRiskMap = (enabled: boolean): TrackingRiskMapState => {
  const [state, setState] = useState<TrackingRiskMapState>({ trackingRisk: null, loaded: false });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    callBackend('telescope:get_performance_envelope', {}, { silent: true, signal: controller.signal })
      .then((envelope) => setState({ trackingRisk: envelope?.trackingRisk ?? null, loaded: true }))
      .catch((err) => {
        if (!(err instanceof Error && err.name === 'AbortError')) {
          console.error('Failed to load the tracking-risk map:', err);
          setState({ trackingRisk: null, loaded: true });
        }
      });
    return () => controller.abort();
  }, [enabled]);

  return state;
};
