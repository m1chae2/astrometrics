import { useQuery } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { catalogQueryPolicy } from './catalogQueryPolicy';
import { fetchTargetDataAvailability } from '../services/astronomyService';

/**
 * Query for the per-target spectra/photometry availability map, used to
 * show a data-presence indicator next to each target in the Astronomy
 * Manager's target filter, instead of a separate "With Spectra"/"With
 * Photometry" catalog-wide filter.
 *
 * A failed request throws, so it is retried and never cached as an empty
 * result. App.tsx invalidates this key on `catalog:changed`. There is no
 * timer and no refetch on window focus.
 *
 * A hidden display does not fetch (see `catalogQueryPolicy`).
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The availability query result.
 */
export const useTargetDataAvailabilityQuery = () => {
    const isDisplayActive = useIsDisplayActive();
    return useQuery({
        queryKey: ['targetDataAvailability'],
        queryFn: fetchTargetDataAvailability,
        ...catalogQueryPolicy(isDisplayActive),
    });
};
