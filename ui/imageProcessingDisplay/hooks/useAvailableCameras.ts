import { useState, useEffect } from 'react';
import { fetchAvailableCameras } from '../../common/services/systemService';

/**
 * Hook to fetch and manage the list of available cameras from the system configuration.
 */
export const useAvailableCameras = () => {
    const [availableCameras, setAvailableCameras] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let mounted = true;
        fetchAvailableCameras()
            .then(cameras => {
                if (mounted) {
                    setAvailableCameras(cameras);
                    setLoading(false);
                }
            })
            .catch(() => {
                if (mounted) setLoading(false);
            });
        return () => { mounted = false; };
    }, []);

    return { availableCameras, loading };
};
