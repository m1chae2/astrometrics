/**
 * @file useFrameStatuses.ts
 * @description Reads each frame's status for the selected target (included, flagged, not stacked yet,
 * set aside), including the frames set aside, as the backend decides it. The answer is kept for each
 * target (see `targetReadQueryPolicy.ts`), so showing the display again or going back to a target
 * already opened does not ask the backend again. A change to this target's data, or `reload`, reads
 * again. Another target's statuses are never shown while this target's are read. A read that fails
 * leaves the value `null`, which the screen shows as "Included", never as an error.
 */

import { useCallback, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { readFrameStatuses } from '../../common/services/imaging/frameReviewService';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import type { FrameStatusReport } from '../../common/types/backendTypes';
import { buildFrameStatusLookup, type FrameStatusInfo } from '../utils/frameStatus';
import { TARGET_READ_QUERY_OPTIONS } from './targetReadQueryPolicy';

/** What {@link useFrameStatuses} gives back. */
export interface FrameStatuses {
    /** The raw per-frame statuses from the backend, or `null` until read. */
    report: FrameStatusReport | null;
    /** Each frame's status by file path. */
    statuses: Map<string, FrameStatusInfo>;
    /** Reads again, for example after a frame is put back. */
    reload: () => void;
}

/**
 * Reads the frame statuses of one target.
 * @param targetId The selected target ("" for none).
 * @param shouldFetch False while the target is not local, so nothing is asked of the backend.
 * @return The report, the status of each frame, and a function that reads again.
 */
export function useFrameStatuses(
    targetId: string,
    shouldFetch: boolean = true,
): FrameStatuses {
    const queryClient = useQueryClient();
    const isWanted = Boolean(targetId) && shouldFetch;
    const statusQuery = useQuery({
        queryKey: qualityQueryKeys.frameStatuses(targetId),
        queryFn: () => readFrameStatuses(targetId),
        enabled: isWanted,
        ...TARGET_READ_QUERY_OPTIONS,
    });

    /** Marks the kept statuses of this target as old, so they are read again now. */
    const reload = useCallback(() => {
        void queryClient.invalidateQueries({ queryKey: qualityQueryKeys.frameStatuses(targetId), exact: true });
    }, [queryClient, targetId]);

    const report = isWanted ? (statusQuery.data ?? null) : null;
    const statuses = useMemo(() => buildFrameStatusLookup(report), [report]);
    return { report, statuses, reload };
}
