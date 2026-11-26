/**
 * @module useClassStarColorsQuery
 * @fileoverview Query for the stars the Planetarium colors in its "By
 * spectral class" mode: every star in the spectral class lists and every
 * star with a spectrum, with the facts that set each one's color.
 */

import { useQuery } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { catalogQueryPolicy } from './catalogQueryPolicy';
import { callBackend } from '../services/backendApi';
import type { ClassStarColorRow } from '../types/planetariumTypes';

/** How often a failed request is tried again, in milliseconds (15 seconds). */
export const CLASS_STARS_RETRY_INTERVAL_MS = 15000;

/**
 * Fetches the stars from `planetarium:get_class_stars`.
 *
 * Throws on failure, so a failed request is retried and never kept as an
 * empty answer. It is sent quietly (no error pop-up): the Planetarium shows
 * a short note in its color legend instead.
 *
 * @returns {Promise<ClassStarColorRow[]>} The stars.
 */
export async function fetchClassStarColors(): Promise<ClassStarColorRow[]> {
    const data = await callBackend('planetarium:get_class_stars', {}, { silent: true });
    return Array.isArray(data) ? data : [];
}

/**
 * Query for the class stars, asked only while `enabled` is true.
 *
 * App.tsx invalidates this key on `catalog:changed`. While the answer is
 * good there is no timer and no refetch on window focus; after a failure it
 * asks again every CLASS_STARS_RETRY_INTERVAL_MS and on window focus. A hidden
 * display does not fetch.
 *
 * @param {boolean} enabled Whether the spectral class mode is on.
 * @returns {import('@tanstack/react-query').UseQueryResult} The query result.
 */
export const useClassStarColorsQuery = (enabled: boolean) => {
    const isDisplayActive = useIsDisplayActive();
    const policy = catalogQueryPolicy(isDisplayActive);
    return useQuery({
        queryKey: ['classStarColors'],
        queryFn: fetchClassStarColors,
        ...policy,
        enabled: enabled && policy.enabled,
        // After a failure (for example a backend that is restarting) it tries again on its own,
        // and when the window is focused again, so the legend never stays on an old error.
        refetchOnWindowFocus: (query) => query.state.status === 'error',
        refetchInterval: (query) => (query.state.status === 'error' ? CLASS_STARS_RETRY_INTERVAL_MS : false),
    });
};
