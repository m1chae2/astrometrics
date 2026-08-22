/**
 * @fileoverview Service for guiding control and telemetry polling.
 * Communicates via JSON-RPC.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from '../backendApi';
import { reportError } from '../../utils/reportError';

/**
 * Commands the autoguider to start the guiding loop.
 * @param exposure Exposure duration in seconds.
 * @param gain Gain multiplier.
 * @return Success indicator.
 */
export async function startGuiding(exposure: number, gain: number): Promise<boolean> {
    try {
        return await callBackend("guiding:start", { exposure, gain });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to start guiding: ${msg}`), 'backend');
        return false;
    }
}

/**
 * Commands the autoguider to stop the guiding loop.
 * @return Success indicator.
 */
export async function stopGuiding(): Promise<boolean> {
    try {
        return await callBackend("guiding:stop", {});
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to stop guiding: ${msg}`), 'backend');
        return false;
    }
}

/**
 * Commands a single guide-camera exposure, independent of the guiding loop.
 * @param exposure Exposure duration in seconds.
 * @param gain Optional gain; omitted leaves the camera's current gain.
 * @return Success indicator.
 */
export async function captureGuideFrame(exposure: number, gain?: number): Promise<boolean> {
    try {
        return await callBackend("guiding:capture_frame", { exposure, gain });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to capture guide frame: ${msg}`), 'backend');
        return false;
    }
}

/**
 * Fetches the current autoguider tracking status and error drift history.
 * @return Mapped status and history payload.
 */
export async function fetchGuidingStatus(): Promise<Record<string, unknown>> {
    try {
        const data = await callBackend("guiding:status", {});
        return (data as Record<string, unknown>) || {};
    } catch {
        // Suppress polling errors to avoid console spam.
        return {};
    }
}
