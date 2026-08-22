/**
 * @file useTargetVisibility.ts
 * @description Hook to fetch and poll for currently visible targets from the backend.
 * Provides real-time tracking of altitude, azimuth, and set times for the library.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
import { useState, useEffect } from 'react';
import { fetchVisibleTargets, VisibleTarget } from '../../common/services/targetService';

/**
 * Hook to fetch and poll for currently visible targets.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
export const useTargetVisibility = () => {
    const [visibleTargets, setVisibleTargets] = useState<VisibleTarget[]>([]);
    const [loading, setLoading] = useState<boolean>(true);

    useEffect(() => {
        let isMounted = true;

        const loadVisible = async () => {
            try {
                const data = await fetchVisibleTargets();
                if (isMounted) {
                    setVisibleTargets(data);
                    setLoading(false);
                }
            } catch (err) {
                console.error("Error loading visible targets", err);
                if (isMounted) setLoading(false);
            }
        };

        loadVisible();
        const intervalId = setInterval(loadVisible, 30000); // Pulse every 30s

        return () => {
            isMounted = false;
            clearInterval(intervalId);
        };
    }, []);

    return { visibleTargets, loading };
};
