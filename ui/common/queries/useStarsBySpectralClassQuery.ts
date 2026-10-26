import { useQuery } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { catalogQueryPolicy } from './catalogQueryPolicy';
import { fetchStarsBySpectralClass } from '../services/astronomyService';

/**
 * Query for the stars in one catalog spectral class, best self-determined
 * match first.
 *
 * A failed request throws, so it is retried and never cached as an empty
 * result. App.tsx invalidates this key on `catalog:changed`. There is no
 * timer and no refetch on window focus.
 *
 * @param spectralClass The spectral class letter, or undefined when no class is selected.
 * @returns {import('@tanstack/react-query').UseQueryResult} The class's star list query result.
 */
export const useStarsBySpectralClassQuery = (spectralClass: string | undefined) => {
    const isDisplayActive = useIsDisplayActive();
    const policy = catalogQueryPolicy(isDisplayActive);
    return useQuery({
        queryKey: ['starsBySpectralClass', spectralClass],
        queryFn: () => fetchStarsBySpectralClass(spectralClass as string),
        ...policy,
        enabled: !!spectralClass && policy.enabled,
    });
};
