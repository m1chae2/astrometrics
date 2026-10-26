/**
 * @file useTargetVisibility.ts
 * @description Hook to fetch and poll for currently visible targets from the backend.
 * Provides real-time tracking of altitude, azimuth, and set times for the library.
 */
import { useCallback, useState } from 'react';
import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { usePollTick } from '../../common/hooks/usePollTick';
import { callBackend } from '../../common/services/backendApi';
import { ObjectVisibility } from '../../common/types/backendTypes';

/** How often to re-poll visible targets, in milliseconds. */
const VISIBILITY_POLL_INTERVAL_MS = 30000;

/**
 * Hook to fetch and poll for currently visible targets.
 * @return The targets, whether a fetch is running, the error text of the last fetch (or null) and `retry`.
 */
export const useTargetVisibility = () => {
    const pollTick = usePollTick(VISIBILITY_POLL_INTERVAL_MS);
    const [retryCount, setRetryCount] = useState(0);
    const retry = useCallback(() => setRetryCount((count) => count + 1), []);

    const { data, loading, error } = useBackendFetch<ObjectVisibility[]>(
        async (signal) => (await callBackend('astronomy:visible', {}, { timeoutMs: 30000, signal })) ?? [],
        [pollTick, retryCount],
        { errorMessage: 'Failed to load visible targets' }
    );

    return { visibleTargets: data ?? [], loading, error, retry };
};
