import { useQuery } from '@tanstack/react-query';
import { fetchTargetDataAvailability } from '../services/astronomyService';

/**
 * Query for the per-target spectra/photometry availability map, used to
 * show a data-presence indicator next to each target in the Astronomy
 * Manager's target filter, instead of a separate "With Spectra"/"With
 * Photometry" catalog-wide filter.
 *
 * `fetchTargetDataAvailability` swallows a failed request into an empty
 * map rather than throwing, so React Query would otherwise treat a
 * transient backend hiccup as a permanently cached "no data" result;
 * polling and refetch-on-focus (matching `useAstronomyListQuery`) give it
 * a chance to recover instead of sticking with an empty map forever.
 *
 * Freshness after a real catalog change no longer depends on this
 * interval: App.tsx refetches immediately on the backend's `catalog:changed`
 * broadcast, which fires whenever the catalog actually changed (see
 * `StellarService._cached_catalog_answer`). `refetchInterval` is
 * only the backup for a missed broadcast (e.g. the socket briefly
 * dropped), so it can be long.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The availability query result.
 */
export const useTargetDataAvailabilityQuery = () =>
    useQuery({
        queryKey: ['targetDataAvailability'],
        queryFn: fetchTargetDataAvailability,
        refetchInterval: 120000,
        refetchOnWindowFocus: true,
    });
