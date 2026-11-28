/**
 * @file useStarShapeReview.ts
 * @description Reads the stored star-shape verdicts of the selected target for the Frame Analysis panel.
 * The answer is kept for each target (see `targetReadQueryPolicy.ts`), so showing the display again or
 * going back to a target already opened does not ask the backend again. A change to this target's data
 * marks the kept answer old, and it is read again. Another target's numbers are never shown while this target's are read. A read that
 * fails leaves the value `null`, which the screen shows as "not measured yet", never as an error.
 *
 * The read waits until the display is on screen (`useIsDisplayActive`). Every display is mounted at
 * start-up, and this read can take seconds, so a hidden Image Processing display must not slow the
 * start of the app. A kept answer still shows while the display is hidden, and a change marked while
 * it is hidden is read once it is shown. Once start-up is over, the selected target's verdicts are
 * also read ahead while the browser is idle (`useTargetReadPrefetch.ts`), so the first visit shows
 * them at once.
 */

import { useQuery } from '@tanstack/react-query';
import type { StarShapeReview } from '../../common/types/backendTypes';
import { starShapesQuery } from './targetReadQueryPolicy';
import { useIsDisplayActive } from '../../common/context/DisplayActiveContext';

/**
 * Reads the star-shape review of one target.
 * @param targetId The selected target ("" for none).
 * @param shouldFetch False while the target is not local, so nothing is asked of the backend.
 * @return The review, or `null` until it is read (or when it is not available).
 */
export function useStarShapeReview(targetId: string, shouldFetch: boolean = true): StarShapeReview | null {
    const isWanted = Boolean(targetId) && shouldFetch;
    // Ask the backend only while the display is on screen. A kept answer shows either way.
    const isDisplayActive = useIsDisplayActive();
    const reviewQuery = useQuery({
        ...starShapesQuery(targetId),
        enabled: isWanted && isDisplayActive,
    });
    if (!isWanted) return null;
    return reviewQuery.data ?? null;
}
