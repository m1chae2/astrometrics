/**
 * @fileoverview Service for commanding camera exposures via the JSON-RPC backend.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from '../backendApi';
import { reportError } from '../../utils/reportError';
import { FilterType, FrameType } from '../../types/backendTypes';

/** Exposure of each frame of the open-ended capture, in seconds. */
export const OPEN_ENDED_CAPTURE_EXPOSURE_SECONDS = 60;

/** Filter of the open-ended capture: luminance. The library matches "L" to the wheel slot, however the wheel names it. */
export const OPEN_ENDED_CAPTURE_FILTER = FilterType.L;

/** Frame type of the open-ended capture. */
export const OPEN_ENDED_CAPTURE_IMAGE_TYPE = FrameType.LIGHT;

/**
 * Frame count of the open-ended capture. The backend sets no maximum, so this is only a backstop for a run
 * the user forgets: at 60 s per frame it lasts about a week. The user ends the run with Stop Capture.
 */
export const OPEN_ENDED_CAPTURE_FRAME_COUNT = 10000;

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

/**
 * Starts a capture that runs until the user stops it: 60 s luminance light frames, with a large frame count
 * as the only limit. The settings are fixed; Observation Planning will supply them later.
 *
 * @param targetId Target the frames are recorded against.
 * @return The job id, or null if the capture could not be started (the failure is already shown).
 */
export async function startOpenEndedCapture(targetId: string): Promise<string | null> {
    return startCaptureSequence({
        targetId,
        exposureSeconds: OPEN_ENDED_CAPTURE_EXPOSURE_SECONDS,
        count: OPEN_ENDED_CAPTURE_FRAME_COUNT,
        imageType: OPEN_ENDED_CAPTURE_IMAGE_TYPE,
        filterName: OPEN_ENDED_CAPTURE_FILTER,
    });
}

/**
 * Stops the capture run the app started and aborts its exposure. The frames already taken are kept.
 *
 * @return True if a run was stopped, false if none was running or the stop failed (`callBackend` shows a failure).
 */
export async function stopCapture(): Promise<boolean> {
    try {
        return await callBackend('imaging:stop_capture', {});
    } catch {
        // callBackend has already shown the reason once.
        return false;
    }
}
