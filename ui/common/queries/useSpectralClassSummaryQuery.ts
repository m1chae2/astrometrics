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
 * @returns {import('@tanstack/react-query').UseQueryResult} The spectral class summary query result.
 */
export const useSpectralClassSummaryQuery = () =>
    useQuery({
        queryKey: ['spectralClassSummary'],
        queryFn: fetchSpectralClassSummary,
        refetchInterval: 30000,
        refetchOnWindowFocus: true,
    });
