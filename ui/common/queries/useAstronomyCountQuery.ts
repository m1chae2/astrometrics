import { useQuery } from '@tanstack/react-query';
import { useIsDisplayActive } from '../context/DisplayActiveContext';
import { catalogQueryPolicy } from './catalogQueryPolicy';
import { fetchAstronomyCount } from '../services/astronomyService';

/**
 * Query for the total number of stars that match one scope (a target or the
 * whole catalog) and a search text, used to show "Page X / Y".
 *
 * The count is cached per scope and search, so it is fetched once for each
 * change of scope or search text and not on every render. There is no timer;
 * App.tsx invalidates the `astronomyCount` key on `catalog:changed`.
 *
 * @param targetId The target to scope the count to, or undefined for the whole catalog.
 * @param search The search text, or an empty string for none.
 * @returns {import('@tanstack/react-query').UseQueryResult<number>} The count query result.
 */
export const useAstronomyCountQuery = (targetId: string | undefined, search: string) => {
    const isDisplayActive = useIsDisplayActive();
    return useQuery({
        queryKey: ['astronomyCount', targetId ?? 'all', search],
        queryFn: () => fetchAstronomyCount({ targetId, search }),
        ...catalogQueryPolicy(isDisplayActive),
    });
};
