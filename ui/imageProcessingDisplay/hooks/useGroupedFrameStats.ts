/**
 * @file useGroupedFrameStats.ts
 * @description Reads the selected target's light frames grouped by gain, filter and exposure, for the
 * stacking panel and the calibration line. Both use this hook, so they share one kept answer. The answer
 * is kept for each target (see `targetReadQueryPolicy.ts`), so showing the display again or going back to
 * a target already opened does not ask the backend again. New frames or a restack of this target mark the
 * kept answer old, and it is read again; other targets keep theirs. Requests that run at the same time
 * are still merged into one call (`requestFrameStatsGrouped`). A failed read is reported once by the service and is not kept.
 */

import { useQuery } from '@tanstack/react-query';
import { requestFrameStatsGrouped } from '../../common/services/targetService';
import type { GroupedFrameStat } from '../../common/types/backendTypes';
import { groupedFramesQueryKey } from '../../common/queries/targetReadQueryKeys';
import { TARGET_READ_QUERY_OPTIONS } from './targetReadQueryPolicy';

/** What {@link useGroupedFrameStats} gives back. */
export interface GroupedFrameStats {
    /** The groups, or `undefined` while there is no answer for this target. */
    groups: GroupedFrameStat[] | undefined;
    /** True while a read is running and there is no kept answer to show yet. */
    isLoading: boolean;
}

/**
 * Reads the grouped light-frame summary of one target.
 * @param targetId The selected target ("" for none).
 * @param shouldFetch False while the target is not local, so nothing is asked of the backend.
 * @return The groups and whether a first read is still running.
 */
export function useGroupedFrameStats(targetId: string, shouldFetch: boolean = true): GroupedFrameStats {
    const isWanted = Boolean(targetId) && shouldFetch;
    const groupsQuery = useQuery({
        queryKey: groupedFramesQueryKey(targetId),
        queryFn: () => requestFrameStatsGrouped(targetId),
        enabled: isWanted,
        ...TARGET_READ_QUERY_OPTIONS,
    });
    if (!isWanted) return { groups: undefined, isLoading: false };
    // `isPending` is true on the very render the read becomes wanted, before it starts, so a screen
    // waiting for "loaded" never sees a false "done". After a failure, a new try is fetching with no data.
    const isLoading = groupsQuery.data === undefined && (groupsQuery.isPending || groupsQuery.isFetching);
    return { groups: groupsQuery.data, isLoading };
}
