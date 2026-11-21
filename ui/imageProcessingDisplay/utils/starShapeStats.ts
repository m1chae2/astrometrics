/**
 * @file starShapeStats.ts
 * @description Small numbers drawn from the star-shape review's per-frame series: the median, and the
 * session's typical elongation (the dashed line of the stretch-by-frame chart and the "session typical"
 * in a flagged frame's hover text). Plain functions, so they can be tested without a screen.
 */

import type { FrameStarShape } from '../../common/types/backendTypes';

/**
 * Finds the middle value of a list of numbers.
 * @param values The numbers.
 * @return The median, or `null` for an empty list.
 */
export function median(values: number[]): number | null {
    if (values.length === 0) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * Finds a frame's measured elongation, if it has one.
 * @param frame One frame of the review's series.
 * @return The elongation, or `null`.
 */
function elongationOf(frame: FrameStarShape): number | null {
    return typeof frame.elongation === 'number' && Number.isFinite(frame.elongation) ? frame.elongation : null;
}

/**
 * Finds the session's typical elongation: the median over the measured frames.
 * @param series The review's per-frame numbers.
 * @return The median, or `null` when no frame has one.
 */
export function sessionTypicalElongation(series: FrameStarShape[] | undefined): number | null {
    return median((series ?? []).map(elongationOf).filter((value): value is number => value !== null));
}
