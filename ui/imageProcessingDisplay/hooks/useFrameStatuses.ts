/**
 * @file useFrameStatuses.ts
 * @description Reads each frame's status for the selected target (included, flagged, not stacked yet,
 * set aside), including the frames set aside, as the backend decides it. The answer is kept for each
 * target (see `targetReadQueryPolicy.ts`), so showing the display again or going back to a target
 * already opened does not ask the backend again. A change to this target's data, or `reload`, reads
 * again. Another target's statuses are never shown while this target's are read. A read that fails
 * leaves the value `null`, which the screen shows as "Included", never as an error.
 *
 * The read waits until the display is on screen (`useIsDisplayActive`). Every display is mounted at
 * start-up, so a hidden Image Processing display must not slow the start of the app. A kept answer
 * still shows while the display is hidden, and a change (or `reload`) marked while it is hidden is
 * read once it is shown. Once start-up is over, the selected target's statuses are also read ahead
 * while the browser is idle (`useTargetReadPrefetch.ts`), so the first visit shows them at once.
 */

import { useCallback, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import type { FrameStatusReport } from '../../common/types/backendTypes';
import { buildFrameStatusLookup, type FrameStatusInfo } from '../utils/frameStatus';
import { frameStatusesQuery } from './targetReadQueryPolicy';
import { useIsDisplayActive } from '../../common/context/DisplayActiveContext';

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
    // Ask the backend only while the display is on screen. A kept answer shows either way.
    const isDisplayActive = useIsDisplayActive();
    const statusQuery = useQuery({
        ...frameStatusesQuery(targetId),
        enabled: isWanted && isDisplayActive,
    });

    /** Marks the kept statuses of this target as old, so they are read again now. */
    const reload = useCallback(() => {
        void queryClient.invalidateQueries({ queryKey: qualityQueryKeys.frameStatuses(targetId), exact: true });
    }, [queryClient, targetId]);

    const report = isWanted ? (statusQuery.data ?? null) : null;
    const statuses = useMemo(() => buildFrameStatusLookup(report), [report]);
    return { report, statuses, reload };
}
