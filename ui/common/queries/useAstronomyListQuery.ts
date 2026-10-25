import { useQuery } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { catalogQueryPolicy } from './catalogQueryPolicy';
import { fetchAstronomyList, type AstronomyListOptions } from '../services/astronomyService';

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

    const effectiveTargetId = opts.targetId || 'all';
    const effectiveSearch = opts.search || '';
    const effectiveFilterType = opts.filterType || 'all';
    const effectiveLimit = opts.limit !== undefined ? opts.limit : 100;
    const effectiveOffset = opts.offset !== undefined ? opts.offset : 0;

    const isDisplayActive = useIsDisplayActive();
    return useQuery({
        queryKey: ['astronomyList', effectiveTargetId, effectiveSearch, effectiveFilterType, effectiveLimit, effectiveOffset],
        queryFn: () => fetchAstronomyList(opts),
        // No timer: the catalog is large, so a poll made requests pile up
        // on the backend. App.tsx invalidates this key when the catalog changes.
        ...catalogQueryPolicy(isDisplayActive),
    });
};
