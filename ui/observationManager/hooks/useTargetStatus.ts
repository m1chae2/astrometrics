/**
 * @fileoverview Hook for retrieving real-time Alt/Az and visibility status of a selected target.
 * Aligns with the Google TypeScript Style Guide.
 */

import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { usePollTick } from '../../common/hooks/usePollTick';
import { callBackend } from '../../common/services/backendApi';
import { fetchTargetObject } from '../../common/services/targetService';

/** How often to re-poll target status, in milliseconds. */
const STATUS_POLL_INTERVAL_MS = 10000;

interface TargetStatusData {
    ra: string;
    dec: string;
    alt?: string;
    az?: string;
    riseTime?: string;
    setTime?: string;
    visible: boolean;
}

/**
 * Hook to manage real-time Alt/Az tracking and rise/set calculations.
 * @param selectedTargetId Optional unique target identifier.
 */
export const useTargetStatus = (selectedTargetId?: string) => {
    const pollTick = usePollTick(STATUS_POLL_INTERVAL_MS);

    const { data: status } = useBackendFetch<TargetStatusData | null>(
        async (signal) => {
            if (!selectedTargetId) return null;

            const targetData = await fetchTargetObject(selectedTargetId);
            if (!targetData) return null;

            try {
                const statusData = await callBackend(
                    "astronomy:get_status",
                    { target_id: selectedTargetId },
                    { signal, silent: true }
                );
                if (statusData) {
                    return {
                        ra: targetData.ra as string,
                        dec: targetData.dec as string,
                        alt: `${statusData.altitude_deg.toFixed(1)}°`,
                        az: `${statusData.azimuth_deg.toFixed(1)}°`,
                        riseTime: statusData.rise_utc,
                        setTime: statusData.set_utc,
                        visible: statusData.above_horizon
                    };
                }
            } catch (err) {
                if (err instanceof Error && err.name === 'AbortError') throw err;
                console.warn("Failed to fetch astronomy status", err);
            }

            return { ra: targetData.ra as string, dec: targetData.dec as string, alt: 'N/A', az: 'N/A', visible: false };
        },
        [selectedTargetId, pollTick],
        { errorMessage: 'Failed to load target status' }
    );

    return { status: status ?? null };
};
