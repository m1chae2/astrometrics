/**
 * @fileoverview Service for commanding camera exposures via the JSON-RPC backend.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from '../backendApi';
import { reportError } from '../../utils/reportError';

/** Parameters describing a capture request. */
export interface CaptureRequest {
    /** Target the frames are recorded against. */
    targetId: string;
    /** Exposure duration in seconds. */
    exposureSeconds: number;
    /** Number of frames to take. */
    count: number;
    /** Frame type: LIGHT, DARK, FLAT, or BIAS. */
    imageType?: string;
    /** Filter to select before the sequence starts. */
    filterName?: string;
    /** Settling pause between consecutive frames, in seconds. */
    delaySeconds?: number;
}

/**
 * Starts a capture sequence on the imaging camera.
 *
 * Returns the backend's job identifier rather than waiting for the frames:
 * the sequence runs asynchronously and its progress is polled through
 * `imaging:get_active_jobs`.
 *
 * @param request Capture parameters.
 * @return The job id, or null if the request could not be started.
 */
export async function startCaptureSequence(request: CaptureRequest): Promise<string | null> {
    try {
        return await callBackend('imaging:capture', {
            target_id: request.targetId,
            exposure_seconds: request.exposureSeconds,
            count: request.count,
            image_type: request.imageType,
            filter_name: request.filterName,
            delay_seconds: request.delaySeconds,
        });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to start capture sequence: ${msg}`), 'backend');
        return null;
    }
}

/**
 * Takes a single test frame using the supplied sequence settings.
 *
 * Forces a count of one regardless of what the sequence row specifies: a test
 * frame exists to check framing, focus, and exposure before committing to the
 * full run. The row's filter is still honoured, since framing through the
 * wrong filter would defeat the purpose, but its inter-frame delay is dropped
 * as meaningless for a single frame.
 *
 * @param request Capture parameters; `count` and `delaySeconds` are ignored.
 * @return The job id, or null if the request could not be started.
 */
export async function captureTestFrame(
    request: Omit<CaptureRequest, 'count' | 'delaySeconds'>
): Promise<string | null> {
    return startCaptureSequence({ ...request, count: 1, delaySeconds: 0 });
}
