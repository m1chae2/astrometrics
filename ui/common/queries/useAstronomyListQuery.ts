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
        // 3000ms polled the *entire* stellar-object catalog every 3
        // seconds regardless of size. At the catalog scale a full
        // identification pass can now produce (270k+ objects on
        // 2026-08-25), that meant this view was almost always mid-fetch
        // on a new request before the previous one finished, competing
        // with the same request other open tabs/views were making.
        // 30s matches useRemoteTargetsQuery's polling interval for
        // similar background-refresh data; refetchOnWindowFocus below
        // still gives an immediate refresh when the user actually
        // returns to this view.
        refetchInterval: 30000,
        refetchOnWindowFocus: true,
    });
