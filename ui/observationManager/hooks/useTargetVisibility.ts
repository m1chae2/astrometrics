/**
 * @file useTargetVisibility.ts
 * @description Hook to fetch and poll for currently visible targets from the backend.
 * Provides real-time tracking of altitude, azimuth, and set times for the library.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { usePollTick } from '../../common/hooks/usePollTick';
import { fetchVisibleTargets } from '../../common/services/targetService';
import { ObjectVisibility } from '../../common/types/backendTypes';

/** How often to re-poll visible targets, in milliseconds. */
const VISIBILITY_POLL_INTERVAL_MS = 30000;

/**
 * Hook to fetch and poll for currently visible targets.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
export const useTargetVisibility = () => {
    const pollTick = usePollTick(VISIBILITY_POLL_INTERVAL_MS);

    const { data, loading } = useBackendFetch<ObjectVisibility[]>(
        () => fetchVisibleTargets(),
        [pollTick],
        { errorMessage: 'Failed to load visible targets' }
    );

    return { visibleTargets: data ?? [], loading };
};
