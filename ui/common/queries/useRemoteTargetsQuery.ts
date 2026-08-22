import { useQuery } from '@tanstack/react-query';
import { scanRemoteTargets } from '../services/imagingService';

/**
 * Shared, periodically-refreshed query for the remote telescope's
 * available image folders.
 *
 * Replaces RemoteStatusContext's previous hand-rolled 30-second polling
 * loop with the query library's built-in refetch interval, while keeping
 * the same one-shared-poll-for-all-consumers behavior.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The remote targets query result.
 */
export const useRemoteTargetsQuery = () =>
    useQuery({
        queryKey: ['remoteTargets'],
        queryFn: scanRemoteTargets,
        refetchInterval: 30000,
    });
