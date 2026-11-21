/**
 * @file starShapeReviewService.ts
 * @description Calls the backend for the stored star-shape verdicts of a target: how round its stars
 * are, whether they stretch one way, which frames stand out, and the per-frame numbers for a chart.
 * The read is quiet: a target with nothing measured yet is a status, not an error, so it answers
 * `null` when the backend has nothing to say.
 */

import { callBackend } from '../backendApi';
import type { StarShapeReview } from '../../types/backendTypes';

/**
 * Reads the plain star-shape verdicts of a target, with the per-frame numbers for the chart.
 * @param targetId The target.
 * @return The review, or `null` when it could not be read.
 */
export async function fetchStarShapeReview(targetId: string): Promise<StarShapeReview | null> {
    try {
        return (await callBackend('advisory:star_shapes', { target: targetId }, { silent: true })) ?? null;
    } catch {
        return null;
    }
}
