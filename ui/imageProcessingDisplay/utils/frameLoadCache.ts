/**
 * @file frameLoadCache.ts
 * @description Keeps the two frames of a Blink comparison loaded, so switching between them is instant.
 * Only frames named in `preloadFrames` are kept, so the viewer still reads every other file fresh. The
 * file is read once for each display mode; the decoded picture of a Linear file is made ahead of time
 * by the viewer's own prefetch (`prefetchDecode`).
 */

import { fetchFitsFileByPath, fetchImageByPath } from '../../common/services/imaging/imageService';
import { prefetchDecode } from '../../common/fitsViewer/hooks/useFitsLoader';
import type { RenderedImage } from '../../common/types/backendTypes';

/** A frame ready for the viewer: the FITS file (Linear) or the picture the backend drew (Auto Stretch). */
export type LoadedFrame = { kind: 'fits'; blob: Blob } | { kind: 'picture'; image: RenderedImage | null };

const held = new Map<string, Promise<LoadedFrame>>();

/**
 * Builds the cache key of a frame.
 * @param path The frame's path.
 * @param stretch True for the Auto Stretch picture, false for the Linear FITS file.
 * @return The key.
 */
function keyOf(path: string, stretch: boolean): string {
    return `${stretch ? 'picture' : 'fits'}:${path}`;
}

/**
 * Reads a frame from the backend.
 * @param path The frame's path.
 * @param stretch True for the Auto Stretch picture, false for the Linear FITS file.
 * @return The frame.
 */
async function readFrame(path: string, stretch: boolean): Promise<LoadedFrame> {
    if (stretch) return { kind: 'picture', image: await fetchImageByPath(path, true) };
    return { kind: 'fits', blob: await fetchFitsFileByPath(path) };
}

/**
 * Gives a frame that was preloaded, if there is one.
 * @param path The frame's path.
 * @param stretch True for the Auto Stretch picture, false for the Linear FITS file.
 * @return The pending or finished frame, or `null` when it was not preloaded (read it yourself).
 */
export function preloadedFrame(path: string, stretch: boolean): Promise<LoadedFrame> | null {
    return held.get(keyOf(path, stretch)) ?? null;
}

/**
 * Starts reading frames and keeps them until `releaseFrames` is called. A Linear frame is also decoded
 * ahead of time. A read that fails is dropped, so the viewer reads the file itself and shows the error.
 * @param paths The frames to keep.
 * @param stretch True for the Auto Stretch picture, false for the Linear FITS file.
 * @param signal Aborted when the frames are no longer wanted.
 */
export function preloadFrames(paths: readonly string[], stretch: boolean, signal: AbortSignal): void {
    for (const path of paths) {
        const key = keyOf(path, stretch);
        if (held.has(key)) continue;
        const pending = readFrame(path, stretch);
        held.set(key, pending);
        pending.then((frame) => {
            if (frame.kind === 'fits') void prefetchDecode(frame.blob, signal);
            if (frame.kind === 'picture' && frame.image === null) held.delete(key);
        }).catch(() => held.delete(key));
    }
}

/** Lets go of every preloaded frame. */
export function releaseFrames(): void {
    held.clear();
}
