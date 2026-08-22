import { useQuery } from '@tanstack/react-query';
import { fetchAstronomyList } from '../services/astronomyService';

/**
 * Shared query for the astronomy/stellar object list (spectra and
 * photometry metadata).
 *
 * Consumed by both the Astronomy display's star list and the
 * Planetarium's target search panel, so the two views share one cached
 * fetch instead of each requesting it independently.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The astronomy list query result.
 */
export const useAstronomyListQuery = (targetId?: string) =>
    useQuery({
        queryKey: ['astronomyList', targetId || 'all'],
        queryFn: () => fetchAstronomyList(targetId),
        refetchInterval: 3000,
        refetchOnWindowFocus: true,
    });
