/**
 * @file useStarShapeReview.ts
 * @description Reads the stored star-shape verdicts of the selected target for the Frame Analysis panel.
 * The answer is kept for each target (see `targetReadQueryPolicy.ts`), so showing the display again or
 * going back to a target already opened does not ask the backend again. A change to this target's data
 * marks the kept answer old, and it is read again. Another target's numbers are never shown while this target's are read. A read that
 * fails leaves the value `null`, which the screen shows as "not measured yet", never as an error.
 */

import { useQuery } from '@tanstack/react-query';
import { readStarShapeReview } from '../../common/services/imaging/starShapeReviewService';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import type { StarShapeReview } from '../../common/types/backendTypes';
import { TARGET_READ_QUERY_OPTIONS } from './targetReadQueryPolicy';

/**
 * Reads the star-shape review of one target.
 * @param targetId The selected target ("" for none).
 * @param shouldFetch False while the target is not local, so nothing is asked of the backend.
 * @return The review, or `null` until it is read (or when it is not available).
 */
export function useStarShapeReview(targetId: string, shouldFetch: boolean = true): StarShapeReview | null {
    const isWanted = Boolean(targetId) && shouldFetch;
    const reviewQuery = useQuery({
        queryKey: qualityQueryKeys.starShapes(targetId),
        queryFn: () => readStarShapeReview(targetId),
        enabled: isWanted,
        ...TARGET_READ_QUERY_OPTIONS,
    });
    if (!isWanted) return null;
    return reviewQuery.data ?? null;
}
