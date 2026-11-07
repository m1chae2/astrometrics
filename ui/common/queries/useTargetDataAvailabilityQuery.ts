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
 * timer (except for the empty map below) and no refetch on window focus.
 *
 * An empty map is asked again every few seconds. The library may not have
 * been filled yet when the backend started, and the backend does not keep an
 * empty answer, so this is cheap. It stops as soon as a target has an entry.
 *
 * A hidden display does not fetch (see `catalogQueryPolicy`).
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The availability query result.
 */
/** Wait before asking again while the availability map is empty: 5 seconds. */
export const EMPTY_AVAILABILITY_RETRY_MS = 5000;

export const useTargetDataAvailabilityQuery = () => {
    const isDisplayActive = useIsDisplayActive();
    return useQuery({
        queryKey: ['targetDataAvailability'],
        queryFn: fetchTargetDataAvailability,
        ...catalogQueryPolicy(isDisplayActive),
        refetchInterval: (query) =>
            query.state.data !== undefined && Object.keys(query.state.data).length === 0
                ? EMPTY_AVAILABILITY_RETRY_MS
                : false,
    });
};
