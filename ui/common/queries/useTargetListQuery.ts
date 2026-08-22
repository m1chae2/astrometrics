import { useQuery } from '@tanstack/react-query';
import { fetchTargetList } from '../services/targetService';

/**
 * Shared query for the target catalog list.
 *
 * Multiple views (Target, Observatory, Image Processing, and Planetarium
 * displays) all consume this same cached result instead of each
 * independently fetching it on mount, so the target list is only ever
 * requested from the backend once per cache lifetime rather than once per
 * mounted view.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The target list query result.
 */
export const useTargetListQuery = () =>
    useQuery({ queryKey: ['targetList'], queryFn: fetchTargetList });
