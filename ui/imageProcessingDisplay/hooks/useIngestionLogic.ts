import { useState, useEffect } from 'react';
import { scanRemoteTargets } from '../../common/services/imagingService';

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
    const [remoteTargets, setRemoteTargets] = useState<Set<string>>(new Set());

    useEffect(() => {
        const scan = () => {
            scanRemoteTargets().then(res => {
                const targets = new Set<string>();
                if (res.folders && Array.isArray(res.folders)) {
                    res.folders.forEach((f: string) => {
                        // Normalize to space-separated ID
                        const normalized = f.replace(/_/g, ' ').trim();
                        targets.add(normalized);
                    });
                }
                setRemoteTargets(targets);
            }).catch(err => console.error("Failed to scan remote targets:", err));
        };
        scan(); // Initial
        const interval = setInterval(scan, 30000); // Poll every 30s
        return () => clearInterval(interval);
    }, []);

    const openIngestModal = () => setIsIngestModalOpen(true);
    const closeIngestModal = () => setIsIngestModalOpen(false);

    return {
        isIngestModalOpen,
        setIsIngestModalOpen, // Exposed for direct set if needed
        openIngestModal,
        closeIngestModal,
        remoteTargets
    };
};
