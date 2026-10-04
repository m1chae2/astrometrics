import { useQuery } from '@tanstack/react-query';
import { fetchTargetCameraIndex } from '../services/targetService';

/**
 * Shared query for the per-target camera summary used by the target list's
 * camera filter and "Newest" sort.
 *
 * Every view with a target list shares this one cached result.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The camera index query result.
 */
export const useTargetCameraIndexQuery = () =>
    useQuery({
        queryKey: ['targetCameraIndex'],
        queryFn: fetchTargetCameraIndex,
        // The camera filter is an extra: if the request fails (for example an
        // older backend without this method), try once and fall back to an
        // empty index instead of re-sending the failing request.
        retry: false,
    });
