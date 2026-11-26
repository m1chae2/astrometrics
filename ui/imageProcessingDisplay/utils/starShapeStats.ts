/**
 * @file starShapeStats.ts
 * @description The session's typical elongation (the "session typical" in a flagged frame's hover text).
 * The star-shape review works it out and sends it as `typicalElongation`, so the screen reads that number.
 */

import type { FrameStarShape, StarShapeReview } from '../../common/types/backendTypes';

/**
 * Finds the middle value of a list of numbers.
 * @param values The numbers.
 * @return The median, or `null` for an empty list.
 */
function median(values: number[]): number | null {
    if (values.length === 0) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * Gives the session's typical elongation.
 *
 * Pass the star-shape review: its `typicalElongation` is the number. A caller that still passes the
 * review's per-frame series gets the median of the series, the same number the review gives, until it
 * passes the review itself.
 * @param source The star-shape review, or its per-frame series.
 * @return The typical elongation, or `null` when there is none.
 */
export function sessionTypicalElongation(
    source: Pick<StarShapeReview, 'typicalElongation'> | FrameStarShape[] | null | undefined,
): number | null {
    if (source === null || source === undefined) return null;
    if (Array.isArray(source)) {
        return median(
            source
                .map((frame) => frame.elongation)
                .filter((value): value is number => typeof value === 'number' && Number.isFinite(value)),
        );
    }
    const value = source.typicalElongation;
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
