import { useState, useRef, useEffect } from 'react';
import { isSyncing, syncLightFrames } from '../../../common/services/telescopeService';
import { fetchTargetObject } from '../../../common/services/targetService';
import { emitToast } from '../../../common/utils/emitToast';
import { reportError } from '../../../common/utils/reportError';
import { emit as emitEvent } from '../../../common/utils/eventBus';

export const useTargetSync = (
    selectedTarget: string | undefined,
    onSyncComplete: (data: any) => void
) => {
    const [isSyncingState, setIsSyncingState] = useState<boolean>(false);
    const syncPollRef = useRef<number | null>(null);

    /** Polls the backend to determine if a synchronization task is completed. */
    const startSyncPolling = (objectId: string): void => {
        if (syncPollRef.current) window.clearInterval(syncPollRef.current);
        syncPollRef.current = window.setInterval(async () => {
            try {
                const running = await isSyncing(objectId);
                setIsSyncingState(Boolean(running));
                if (!running) {
                    // Refresh details and notify on completion.
                    const obj = await fetchTargetObject(objectId).catch(() => null);
                    if (obj) {
                        onSyncComplete(obj);

                        const commonName = obj.commonName ?? '';
                        const processed = obj.processedImage ?? '';
                        let msg = 'Sync complete';
                        if (commonName) msg += `: ${commonName}`;
                        emitToast(msg, 'success', 'sync');
                    } else {
                        emitToast(`Sync complete: ${objectId}`, 'success', 'sync');
                    }
                    emitEvent('targetsUpdated');
                    if (syncPollRef.current) {
                        window.clearInterval(syncPollRef.current);
                        syncPollRef.current = null;
                    }
                }
            } catch {
                setIsSyncingState(false);
                if (syncPollRef.current) {
                    window.clearInterval(syncPollRef.current);
                    syncPollRef.current = null;
                }
            }
        }, 1000);
    };

    /** Handles the download/sync action. */
    const startSync = async (): Promise<void> => {
        if (!selectedTarget) return;
        try {
            setIsSyncingState(true);
            await syncLightFrames(selectedTarget);
            startSyncPolling(selectedTarget);
        } catch (err) {
            reportError(err, 'backend');
            setIsSyncingState(false);
        }
    };

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (syncPollRef.current) {
                window.clearInterval(syncPollRef.current);
                syncPollRef.current = null;
            }
        };
    }, []);

    return {
        isSyncing: isSyncingState,
        startSync
    };
};
