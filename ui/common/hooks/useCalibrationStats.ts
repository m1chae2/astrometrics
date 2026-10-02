/**
 * @fileoverview Shared hook to fetch calibration library statistics (darks,
 * biases, flats) via JSON-RPC.
 *
 * Used by both Image Processing (manual refresh after a stacking/ingest job,
 * via `reloadKey`) and Observation Manager's calibration panel (which also
 * wants to poll, via `pollIntervalMs`) — previously each display had its own
 * separate copy of this fetch, one of them polling the same backend action
 * independently of the other.
 */

import { useBackendFetch } from './useBackendFetch';
import { usePollTick } from './usePollTick';
import { callBackend } from '../services/backendApi';
import { CalibrationStats } from '../types/backendTypes';

/** Options for {@link useCalibrationStats}. */
export interface UseCalibrationStatsOptions {
    /** When set, also re-fetches on this interval, independent of `reloadKey`. */
    pollIntervalMs?: number;
}

/**
 * Hook to fetch and cache calibration library statistics from the JSON-RPC backend.
 * Refreshes whenever `reloadKey` changes, and additionally on a timer if `pollIntervalMs` is set.
 * @param reloadKey Key used to trigger a status reload.
 * @param options See {@link UseCalibrationStatsOptions}.
 */
export const useCalibrationStats = (reloadKey: number, options?: UseCalibrationStatsOptions) => {
    const { pollIntervalMs } = options ?? {};
    const pollTick = usePollTick(pollIntervalMs);

    const { data, loading, error } = useBackendFetch<CalibrationStats>(
        (signal) =>
            callBackend("calibration:get_stats", {}, { signal }).then(result => {
                if (!result) throw new Error('Failed to fetch stats');
                return result;
            }),
        [reloadKey, pollTick],
        { errorMessage: 'Failed to fetch stats' }
    );

    return { stats: data, loading, error };
};
