/**
 * @file useAstronomyListQuery.ts
 * The shared, cached query for one page of the star list, and a helper that
 * loads a page ahead of time.
 */

import { useQuery, type QueryClient } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { CATALOG_STALE_TIME_MS, catalogQueryPolicy } from './catalogQueryPolicy';
import { fetchAstronomyList, type AstronomyListOptions } from '../services/astronomyService';

/**
 * The query key and fetch function of one page of the star list. The hook
 * below and `prefetchAstronomyList` both use it, so a prefetch fills the very
 * cache entry the list reads.
 *
 * @param opts The list options (target, search, filter type, page size and offset).
 * @returns The query key and fetch function.
 */
const astronomyListQuery = (opts: AstronomyListOptions) => ({
    queryKey: [
        'astronomyList',
        opts.targetId || 'all',
        opts.search || '',
        opts.filterType || 'all',
        opts.limit !== undefined ? opts.limit : 100,
        opts.offset !== undefined ? opts.offset : 0,
    ],
    queryFn: () => fetchAstronomyList(opts),
});

/**
 * Starts loading one page of the star list ahead of time, such as the first
 * page of a target's stars while the pointer rests on the target's row. It
 * does nothing when that page is already cached and fresh.
 *
 * @param queryClient The app's query client.
 * @param opts The list options, the same ones the list will ask for.
 * @returns A promise that settles when the load ends; it never rejects.
 */
export const prefetchAstronomyList = (queryClient: QueryClient, opts: AstronomyListOptions): Promise<void> =>
    queryClient.prefetchQuery({ ...astronomyListQuery(opts), staleTime: CATALOG_STALE_TIME_MS });

/**
 * Shared query for the astronomy/stellar object list (spectra and
 * photometry metadata).
 *
 * Consumed by both the Astronomy display's star list and the
 * Planetarium's target search panel, so the two views share one cached
 * fetch instead of each requesting it independently.
 *
 * @param optionsOrTargetId Optional target ID or query options object.
 * @param search Optional search query string.
 * @param filterType Optional filter category string.
 * @param limit Maximum number of items to fetch (default: 100).
 * @param offset Number of items to skip for pagination (default: 0).
 * @returns {import('@tanstack/react-query').UseQueryResult} The astronomy list query result.
 */
export const useAstronomyListQuery = (
    optionsOrTargetId?: string | AstronomyListOptions,
    search?: string,
    filterType?: string,
    limit: number = 100,
    offset: number = 0
) => {
    const opts: AstronomyListOptions =
        typeof optionsOrTargetId === 'string'
            ? { targetId: optionsOrTargetId, search, filterType, limit, offset }
            : optionsOrTargetId || { limit, offset };

    const isDisplayActive = useIsDisplayActive();
    return useQuery({
        ...astronomyListQuery(opts),
        // No timer: the catalog is large, so a poll made requests pile up
        // on the backend. App.tsx invalidates this key when the catalog changes.
        ...catalogQueryPolicy(isDisplayActive),
    });
};
