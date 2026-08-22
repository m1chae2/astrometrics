/**
 * @file RemoteStatusContext.tsx
 * @description Single source of truth for remote target availability.
 * Shares useRemoteTargetsQuery()'s cached, periodically-refreshed result
 * across all consumers.
 * REQ: IMG-6.1: The system SHALL indicate if remote images are available.
 */
import React, { createContext, useContext, useMemo, ReactNode } from 'react';
import { useRemoteTargetsQuery } from '../queries/useRemoteTargetsQuery';

interface RemoteStatusContextValue {
    /** Set of normalized remote target names (space-separated). */
    remoteTargets: Set<string>;
    /** Raw folder list from the remote scan. */
    remoteFolders: string[];
    /** Whether a scan is currently in-flight. */
    scanning: boolean;
    /** Force an immediate re-scan. */
    refresh: () => Promise<void>;
}

const RemoteStatusContext = createContext<RemoteStatusContextValue | undefined>(undefined);

/**
 * Provider for remote target availability status.
 * Consolidates polling to a single shared query.
 */
export const RemoteStatusProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const remoteTargetsQuery = useRemoteTargetsQuery();

    // Don't clear existing folders on a temporary network failure: fall back
    // to the last successfully fetched data, which is what React Query keeps
    // around by default while a background refetch is failing.
    const remoteFolders = useMemo(() => remoteTargetsQuery.data?.folders ?? [], [remoteTargetsQuery.data]);

    // Normalized names (spaces instead of underscores)
    const remoteTargets = useMemo(
        () => new Set(remoteFolders.map((folder) => folder.replace(/_/g, ' '))),
        [remoteFolders]
    );

    const value: RemoteStatusContextValue = {
        remoteTargets,
        remoteFolders,
        scanning: remoteTargetsQuery.isFetching,
        refresh: async () => {
            await remoteTargetsQuery.refetch();
        },
    };

    return (
        <RemoteStatusContext.Provider value={value}>
            {children}
        </RemoteStatusContext.Provider>
    );
};

/**
 * Hook to consume the RemoteStatusContext.
 */
export const useRemoteStatusContext = () => {
    const context = useContext(RemoteStatusContext);
    if (context === undefined) {
        throw new Error('useRemoteStatusContext must be used within a RemoteStatusProvider');
    }
    return context;
};
