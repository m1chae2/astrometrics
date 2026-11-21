/**
 * @file blinkView.ts
 * @description The plain steps behind the Blink button. A flagged or set-aside frame is compared with a
 * typical frame of its session at one star. These functions say which star to centre in the frame on
 * screen, how the frame is named in the note beside the button, and why a pair could not be made. They
 * are plain functions, so they can be tested without a screen.
 */

import type { BlinkPair } from '../../common/types/backendTypes';
import type { FrameStatusKind } from './frameStatus';

/** How far the viewer zooms in on the star when a flagged frame is first opened (screen pixels per image pixel). */
export const BLINK_START_ZOOM = 4;

/** The part of the viewer's view that Blink sets: a star to put at the center, and maybe a zoom. */
export interface BlinkFocus {
    /** The star's column in the file, counted from 1 (FITS pixels). */
    x: number;
    /** The star's row in the file, counted from 1 (FITS pixels). */
    y: number;
    /** The file's width in pixels. */
    width: number;
    /** The file's height in pixels. */
    height: number;
    /** Names this focus; the viewer applies a focus once for each picture drawn with the same key. */
    key: string;
    /** The zoom to start from, or `undefined` to keep the zoom the person has. */
    scale?: number;
}

/**
 * Tells whether a frame's status opens the Blink comparison.
 * @param kind The frame's status kind, or `undefined` when it has none.
 * @return True for a flagged frame and a set-aside frame.
 */
export function opensBlink(kind: FrameStatusKind | undefined): boolean {
    return kind === 'flagged' || kind === 'set-aside';
}

/**
 * Names a frame by the number in its file name.
 * @param path The frame's path.
 * @return For example "frame 079" for `Light_M81_0079.fits`, or the file name when it holds no number.
 */
export function frameName(path: string): string {
    const fileName = path.split('/').pop() ?? path;
    const stem = fileName.replace(/\.[^.]*$/, '');
    const match = stem.match(/(\d+)(?!.*\d)/);
    return match ? `frame ${match[1]}` : fileName;
}

/**
 * Finds the star's place in whichever frame of the pair is on screen.
 * @param pair The pair the backend made, which must have been found.
 * @param loadedPath The frame the viewer holds now.
 * @param firstScale The zoom to start from, or `undefined` once the person has used Blink.
 * @return The place to center, or `null` when the frame on screen is not one of the pair.
 */
export function focusForLoadedFrame(pair: BlinkPair, loadedPath: string | null, firstScale: number | undefined): BlinkFocus | null {
    if (!pair.found || loadedPath === null || !pair.width || !pair.height) return null;
    if (loadedPath === pair.path && pair.starX != null && pair.starY != null) {
        return { x: pair.starX, y: pair.starY, width: pair.width, height: pair.height, key: `${pair.path}|${pair.typicalPath}|flagged`, scale: firstScale };
    }
    if (loadedPath === pair.typicalPath && pair.typicalStarX != null && pair.typicalStarY != null) {
        return { x: pair.typicalStarX, y: pair.typicalStarY, width: pair.width, height: pair.height, key: `${pair.path}|${pair.typicalPath}|typical` };
    }
    return null;
}

/**
 * Writes the small note beside the Blink button.
 * @param pair The pair the backend made.
 * @param blinkOn True while the typical frame is on screen.
 * @param statusLabel The frame's status chip text, for example "Set aside: trailed" or "Flagged".
 * @return For example "Frame 123 (set aside: trailed)" or "Typical: frame 079".
 */
export function blinkNote(pair: BlinkPair, blinkOn: boolean, statusLabel: string): string {
    if (blinkOn && pair.typicalPath) return `Typical: ${frameName(pair.typicalPath)}`;
    const detail = statusLabel.charAt(0).toLowerCase() + statusLabel.slice(1);
    const name = frameName(pair.path);
    return `${name.charAt(0).toUpperCase()}${name.slice(1)} (${detail})`;
}
