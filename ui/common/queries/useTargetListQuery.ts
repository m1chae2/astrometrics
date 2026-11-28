/**
 * @file useTargetListQuery.ts
 * @description The shared React Query read of the library's target list, and its key.
 */

import { useQuery } from '@tanstack/react-query';
import { fetchTargetList } from '../services/targetService';

/** The key of the shared target list query. Read it to look at the kept list without asking again. */
export const TARGET_LIST_QUERY_KEY = ['targetList'] as const;

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
    useQuery({ queryKey: TARGET_LIST_QUERY_KEY, queryFn: fetchTargetList, refetchOnWindowFocus: false });
