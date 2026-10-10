import { useState } from 'react';
import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { usePollTick } from '../../common/hooks/usePollTick';
import { scanRemoteTargets } from '../../common/services/imaging/ingestionService';

/** How often to re-scan for remote target folders, in milliseconds. */
const REMOTE_SCAN_POLL_INTERVAL_MS = 30000;

/**
 * Hook to manage ingestion modal state and remote target scanning.
 *
 * @returns Object containing ingestion state.
 *
 * @example
 * const { isIngestModalOpen, remoteTargets, openIngestModal } = useIngestionLogic();
 */
export const useIngestionLogic = () => {
    const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);
    const pollTick = usePollTick(REMOTE_SCAN_POLL_INTERVAL_MS);

    const { data } = useBackendFetch<Set<string>>(
        async () => {
            const res = await scanRemoteTargets();
            const targets = new Set<string>();
            if (res.folders && Array.isArray(res.folders)) {
                res.folders.forEach((f: string) => {
                    // Normalize to space-separated ID
                    const normalized = f.replace(/_/g, ' ').trim();
                    targets.add(normalized);
                });
            }
            return targets;
        },
        [pollTick],
        { errorMessage: 'Failed to scan remote targets' }
    );

    const openIngestModal = () => setIsIngestModalOpen(true);
    const closeIngestModal = () => setIsIngestModalOpen(false);

    return {
        isIngestModalOpen,
        setIsIngestModalOpen, // Exposed for direct set if needed
        openIngestModal,
        closeIngestModal,
        remoteTargets: data ?? new Set<string>()
    };
};
