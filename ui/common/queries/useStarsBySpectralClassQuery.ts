import { useQuery } from '@tanstack/react-query';
import { fetchStarsBySpectralClass } from '../services/astronomyService';

/**
 * Query for the stars in one catalog spectral class, best self-determined
 * match first.
 *
 * `fetchStarsBySpectralClass` swallows a failed request into an empty
 * list rather than throwing, so React Query would otherwise treat a
 * transient backend hiccup as a permanently cached "no stars" result;
 * refetch-on-focus (matching `useAstronomyListQuery`) gives it a chance
 * to recover instead of sticking with an empty list forever. No polling
 * interval, since this list is only shown while its class is selected.
 *
 * @param spectralClass The spectral class letter, or undefined when no class is selected.
 * @returns {import('@tanstack/react-query').UseQueryResult} The class's star list query result.
 */
export const useStarsBySpectralClassQuery = (spectralClass: string | undefined) =>
    useQuery({
        queryKey: ['starsBySpectralClass', spectralClass],
        queryFn: () => fetchStarsBySpectralClass(spectralClass as string),
        enabled: !!spectralClass,
        refetchOnWindowFocus: true,
    });
