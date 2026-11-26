/**
 * @file useStarShapeReview.ts
 * @description Reads the stored star-shape verdicts of the selected target for the Frame Analysis panel.
 * It reads again when the target or the reload key changes, and keeps nothing from the previous target.
 * A read that fails leaves the value `null`, which the screen shows as "not measured yet", never as an error.
 */

import { useEffect, useState } from 'react';
import { fetchStarShapeReview } from '../../common/services/imaging/starShapeReviewService';
import type { StarShapeReview } from '../../common/types/backendTypes';

/**
 * Reads the star-shape review of one target.
 * @param targetId The selected target ("" for none).
 * @param reloadKey Changes when the screen should read again.
 * @param shouldFetch False while the target is not local, so nothing is asked of the backend.
 * @return The review, or `null` until it is read (or when it is not available).
 */
export function useStarShapeReview(targetId: string, reloadKey: number, shouldFetch: boolean = true): StarShapeReview | null {
    const [review, setReview] = useState<StarShapeReview | null>(null);

    useEffect(() => {
        if (!targetId || !shouldFetch) {
            setReview(null);
            return;
        }
        let stale = false;
        fetchStarShapeReview(targetId).then((answer) => { if (!stale) setReview(answer); });
        return () => { stale = true; };
    }, [targetId, reloadKey, shouldFetch]);

    // A different target must not show the last target's numbers while its own are read.
    const [ownerTarget, setOwnerTarget] = useState(targetId);
    if (ownerTarget !== targetId) {
        setOwnerTarget(targetId);
        setReview(null);
    }
    return review;
}
