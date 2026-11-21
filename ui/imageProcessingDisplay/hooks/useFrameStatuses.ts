/**
 * @file useFrameStatuses.ts
 * @description Reads each frame's status for the selected target (included, not stacked yet, set aside),
 * including the frames set aside, and joins it with the star-shape review's flags. It reads again when
 * the target or the reload key changes, and keeps nothing from the previous target. A read that fails
 * leaves the value `null`, which the screen shows as "Included", never as an error.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchFrameStatuses } from '../../common/services/imaging/frameReviewService';
import type { FrameStatusReport, StarShapeReview } from '../../common/types/backendTypes';
import { buildFrameStatusLookup, type FrameStatusInfo } from '../utils/frameStatus';

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
 * @param reloadKey Changes when the screen should read again.
 * @param review The star-shape review, whose flagged frames become "Flagged".
 * @param shouldFetch False while the target is not local, so nothing is asked of the backend.
 * @return The report, the status of each frame, and a function that reads again.
 */
export function useFrameStatuses(
    targetId: string,
    reloadKey: number,
    review: StarShapeReview | null,
    shouldFetch: boolean = true,
): FrameStatuses {
    const [report, setReport] = useState<FrameStatusReport | null>(null);
    const [extraReads, setExtraReads] = useState(0);
    const reload = useCallback(() => setExtraReads((count) => count + 1), []);

    useEffect(() => {
        if (!targetId || !shouldFetch) {
            setReport(null);
            return;
        }
        let stale = false;
        fetchFrameStatuses(targetId).then((answer) => { if (!stale) setReport(answer); });
        return () => { stale = true; };
    }, [targetId, reloadKey, extraReads, shouldFetch]);

    // A different target must not show the last target's statuses while its own are read.
    const [ownerTarget, setOwnerTarget] = useState(targetId);
    if (ownerTarget !== targetId) {
        setOwnerTarget(targetId);
        setReport(null);
    }

    const statuses = useMemo(() => buildFrameStatusLookup(report, review), [report, review]);
    return { report, statuses, reload };
}
