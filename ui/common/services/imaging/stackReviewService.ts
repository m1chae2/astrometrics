/**
 * @file stackReviewService.ts
 * @description Calls the backend for the checks a person can run on a finished stack: making the
 * preview picture again, and reading why frames were set aside. The two slow checks start a
 * background job; the answer is the result, or the job to follow.
 */

import { callBackend } from '../backendApi';
import type {
    PreviewRemakeResult,
    ProcessingJob,
    SetAsideFrameRecord,
    StackReviewStart,
} from '../../types/backendTypes';

/** The event the image viewer listens for, so it reads a rewritten picture again. */
export const STACK_PICTURE_REMADE_EVENT = 'stackPictureRemade';

/** The imaging stack or the spectral stack of a target. */
export type StackKind = 'imaging' | 'spectral';

/** The choices for one preview run. A choice left out uses the app setting. */
export interface PreviewRemakeOptions {
    /** Turn the Cosmic Clarity denoise on or off for this run. */
    denoise?: boolean;
    /** The denoise strength for this run, from 0 to 1. */
    denoiseStrength?: number;
    /** Turn the star toning on or off for this run. */
    starToning?: boolean;
}

/**
 * Starts making a target's preview picture again.
 * @param targetId The target.
 * @param kind The imaging or spectral stack.
 * @param options The choices for this one run.
 * @return The result when it finished at once, or the job to follow.
 */
export async function startPreviewRemake(
    targetId: string,
    kind: StackKind,
    options: PreviewRemakeOptions = {}
): Promise<StackReviewStart> {
    // Silent: the panel shows the error beside the button, so no second message pops up.
    return callBackend(
        'processing:remake_preview',
        {
            target_id: targetId,
            kind,
            denoise: options.denoise,
            denoise_strength: options.denoiseStrength,
            star_toning: options.starToning,
        },
        { silent: true }
    );
}

/**
 * Reads one background job.
 * @param jobId The job's id.
 * @return The job, or `null` if the backend has no such job.
 */
export async function fetchJob(jobId: string): Promise<ProcessingJob | null> {
    return callBackend('processing:get_job', { job_id: jobId }, { silent: true });
}

/**
 * Lists the frames set aside for a target, with the reason on record for each.
 * @param targetId The target.
 * @return One record per frame in the target's `_excluded` folders.
 */
export async function fetchSetAsideFrames(targetId: string): Promise<SetAsideFrameRecord[]> {
    return callBackend('quality:set_aside_frames', { target: targetId }, { silent: true });
}

/**
 * Reads the preview remake result out of a finished job.
 * @param job The job.
 * @return The result the job stored, or `null` if it has none.
 */
export function previewRemakeResultOf(job: ProcessingJob): PreviewRemakeResult | null {
    const stored = job.outputMetrics?.result;
    return stored ? (stored as PreviewRemakeResult) : null;
}
