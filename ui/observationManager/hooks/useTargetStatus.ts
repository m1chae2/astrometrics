/**
 * @fileoverview Hook for retrieving real-time Alt/Az and visibility status of a selected target.
 * Aligns with the Google TypeScript Style Guide.
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { fetchTargetObject } from '../../common/services/targetService';

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
    const [status, setStatus] = useState<TargetStatusData | null>(null);

    // Fetch target data when selection changes
    useEffect(() => {
        let isMounted = true;

        /**
         * Asynchronously loads target details and computes Alt/Az coordinates via JSON-RPC.
         */
        const loadStatus = async () => {
            if (!selectedTargetId) {
                if (isMounted) setStatus(null);
                return;
            }

            try {
                const targetData = await fetchTargetObject(selectedTargetId);

                if (targetData) {
                    try {
                        const statusData = await callBackend("astronomy:get_status", { target_id: selectedTargetId });
                        if (statusData && isMounted) {
                            setStatus({
                                ra: statusData.ra,
                                dec: statusData.dec,
                                alt: statusData.alt,
                                az: statusData.az,
                                riseTime: statusData.rise_time,
                                setTime: statusData.set_time,
                                visible: statusData.visible
                            });
                        } else if (isMounted) {
                            setStatus({
                                ra: targetData.ra as string,
                                dec: targetData.dec as string,
                                alt: 'N/A',
                                az: 'N/A',
                                visible: false
                            });
                        }
                    } catch (err) {
                        console.warn("Failed to fetch astronomy status", err);
                        if (isMounted) {
                            setStatus({
                                ra: targetData.ra as string,
                                dec: targetData.dec as string,
                                alt: 'N/A',
                                az: 'N/A',
                                visible: false
                            });
                        }
                    }
                }
            } catch (e) {
                console.warn("Failed to load target status", e);
                if (isMounted) setStatus(null);
            }
        };

        loadStatus();
        const intervalId = setInterval(loadStatus, 10000);

        return () => {
            isMounted = false;
            clearInterval(intervalId);
        };
    }, [selectedTargetId]);

    return { status };
};
