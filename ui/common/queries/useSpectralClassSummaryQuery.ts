import { useQuery } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { catalogQueryPolicy } from './catalogQueryPolicy';
import { fetchSpectralClassSummary } from '../services/astronomyService';

/**
 * Query for the catalog spectral classes present in the library, with
 * counts, used to browse the Astronomy Manager's star catalog by
 * classification instead of by target.
 *
 * A failed request throws, so it is retried and never cached as an empty
 * result. App.tsx invalidates this key on `catalog:changed`. There is no
 * timer and no refetch on window focus.
 *
 * A hidden display does not fetch (see `catalogQueryPolicy`).
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The spectral class summary query result.
 */
export const useSpectralClassSummaryQuery = () => {
    const isDisplayActive = useIsDisplayActive();
    return useQuery({
        queryKey: ['spectralClassSummary'],
        queryFn: fetchSpectralClassSummary,
        ...catalogQueryPolicy(isDisplayActive),
    });
};
