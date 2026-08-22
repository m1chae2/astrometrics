/**
 * @fileoverview Custom React hook to fetch and manage calibration stats via JSON-RPC.
 * Fetches statistics for darks, biases, and flats from the calibration library.
 * Follows the Google TypeScript Style Guide and naming guidelines.
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { CalibrationStats } from '../../common/types/backendTypes';

/**
 * Hook to fetch and cache calibration library statistics from the JSON-RPC backend.
 * Refreshes whenever the reloadKey changes.
 * @param reloadKey Key used to trigger a status reload.
 */
export const useCalibrationStats = (reloadKey: number) => {
    const [stats, setStats] = useState<CalibrationStats | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let mounted = true;
        setLoading(true);

        callBackend("calibration:get_stats", {})
            .then(data => {
                if (mounted) {
                    if (data) {
                        setStats(data);
                    } else {
                        console.warn("Calibration stats RPC returned no data");
                        setError("Failed to fetch stats");
                    }
                    setLoading(false);
                }
            })
            .catch(err => {
                if (mounted) {
                    console.error("Failed to fetch cal stats via RPC", err);
                    setError(String(err));
                    setLoading(false);
                }
            });

        return () => { mounted = false; };
    }, [reloadKey]);

    return { stats, loading, error };
};
