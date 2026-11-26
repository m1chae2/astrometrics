/**
 * @file measurementPath.ts
 * @description Finds the FITS file path behind the picture a viewer shows, so the star profile
 * panel can ask the backend to measure that same file.
 */
import { callBackend } from '../backendApi';

/** The parts of a target record that name its picture files. */
interface TargetPictureFiles {
    stacking?: { processedImage?: string | null; stackedImage?: string | null } | null;
    spectralStacking?: { stackedImage?: string | null } | null;
    frames?: ReadonlyArray<{ path: string }> | null;
}

/**
 * Picks the file a target's Image Viewer shows: the processed picture, else the stack, else a spectral
 * stack, else any frame whose path says "stacked" or "processed".
 *
 * @param target The target record.
 * @returns The file's path, or null when the target has no picture.
 */
export function pickProcessedImagePath(target: TargetPictureFiles): string | null {
    const named = target.stacking?.processedImage || target.stacking?.stackedImage || target.spectralStacking?.stackedImage;
    if (named) return named;
    const stackedFrame = target.frames?.find((frame) => {
        const lowerPath = frame.path.toLowerCase();
        return lowerPath.includes('stacked') || lowerPath.includes('processed');
    });
    return stackedFrame?.path ?? null;
}

/**
 * Looks up the path of the picture the Image Viewer shows for a target.
 *
 * @param targetId The target.
 * @returns The path, or null when the target has no picture.
 */
export async function resolveProcessedImagePath(targetId: string): Promise<string | null> {
    const target = await callBackend('target:get', { target_id: targetId }, { silent: true });
    // A named target is one record; only a call with no target lists them.
    return target && !Array.isArray(target) ? pickProcessedImagePath(target) : null;
}

/**
 * Looks up the path of one of a target's light frames.
 *
 * @param targetId The target.
 * @param iso Gain or ISO of the frame.
 * @param exposure Exposure length of the frame.
 * @param index Which matching frame, by order.
 * @returns The path of the frame's FITS file.
 */
export async function resolveTargetFramePath(targetId: string, iso: string, exposure: string, index = 0): Promise<string> {
    return callBackend('images:get_target_frame', { target_id: targetId, iso, exposure, index }, { silent: true });
}
