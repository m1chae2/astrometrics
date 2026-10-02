import { useQuery } from '@tanstack/react-query';
import { fetchSpectralClassSummary } from '../services/astronomyService';

/**
 * Query for the catalog spectral classes present in the library, with
 * counts, used to browse the Astronomy Manager's star catalog by
 * classification instead of by target.
 *
 * `fetchSpectralClassSummary` swallows a failed request into an empty
 * list rather than throwing, so React Query would otherwise treat a
 * transient backend hiccup as a permanently cached "no classes" result;
 * polling and refetch-on-focus (matching `useAstronomyListQuery`) give it
 * a chance to recover instead of sticking with an empty list forever.
 *
 * Freshness after a real catalog change no longer depends on this
 * interval: App.tsx refetches immediately on the backend's `catalog:changed`
 * broadcast, which fires whenever the catalog actually changed (see
 * `StellarService._get_cached_catalog_summaries`). `refetchInterval` is
 * only the backup for a missed broadcast (e.g. the socket briefly
 * dropped), so it can be long.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The spectral class summary query result.
 */
export const useSpectralClassSummaryQuery = () =>
    useQuery({
        queryKey: ['spectralClassSummary'],
        queryFn: fetchSpectralClassSummary,
        refetchInterval: 120000,
        refetchOnWindowFocus: true,
    });
