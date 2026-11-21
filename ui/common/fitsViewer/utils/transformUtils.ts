/**
 * @file transformUtils.ts
 * @description Small helpers for the Fits Viewer's pan and zoom math, including the CSS
 * transform that lets an overlay drawn for an earlier view follow the image.
 */
import { createPixelToSky } from '../wcs';
import { fitsPixelToTurnedPicture, turnedPictureToFitsPixel, turnedSize } from '../displayTurn';
import type { FitsHeaderCards, SkyPosition } from '../wcs';

export interface Point {
    x: number;
    y: number;
}

/**
 * Clamps a value between min and max.
 */
export const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

/**
 * Transforms screen coordinates (relative to container) to image coordinates (logical pixels).
 */
export const screenToImage = (
    sx: number,
    sy: number,
    panX: number,
    panY: number,
    zoom: number
): Point => {
    return {
        x: (sx - panX) / zoom,
        y: (sy - panY) / zoom
    };
};

/**
 * Transforms image coordinates to screen coordinates.
 */
export const imageToScreen = (
    ix: number,
    iy: number,
    panX: number,
    panY: number,
    zoom: number
): Point => {
    return {
        x: ix * zoom + panX,
        y: iy * zoom + panY
    };
};

/** A pan and zoom: where the image's top-left corner sits on screen, and its scale. */
export interface ViewState {
    panX: number;
    panY: number;
    zoom: number;
}

/**
 * Builds the CSS transform that moves something drawn for one view so it matches another view.
 *
 * The overlays (star circles, labels, detections) are drawn in screen pixels for the view they
 * were last redrawn at. While the user drags or zooms, this transform carries them along with
 * the image until the next redraw. A screen point `s` drawn at the old view lands at
 * `pan + (zoom / drawnZoom) * (s - drawnPan)`. The transform origin must be the top-left corner.
 *
 * @param current The view the image is at now.
 * @param drawn The view the overlay was last drawn for.
 * @returns A CSS transform string, or an empty string when the two views are the same.
 */
export const overlayTransformBetween = (current: ViewState, drawn: ViewState): string => {
    if (!(drawn.zoom > 0)) return '';
    const ratio = current.zoom / drawn.zoom;
    const shiftX = current.panX - drawn.panX * ratio;
    const shiftY = current.panY - drawn.panY * ratio;
    if (ratio === 1 && shiftX === 0 && shiftY === 0) return '';
    return `translate(${shiftX}px, ${shiftY}px) scale(${ratio})`;
};

/** What the view carry-over needs to know about one image: its size in file pixels and its header. */
export interface ImageGeometry {
    /** Width in pixels (file pixels, or bitmap pixels for a plain picture). */
    width: number;
    /** Height in pixels. */
    height: number;
    /** Header cards, when the image is a FITS file; a plain picture has none. */
    header?: FitsHeaderCards;
    /** How far the picture is shown turned, clockwise in degrees (default 0). The fractions below are fractions of the turned picture. */
    turn?: number;
}

/** A point of an image as fractions of the picture shown (turned, when it is turned) from its top-left corner. */
export interface Fraction {
    x: number;
    y: number;
}

/**
 * Tells whether the image keeps its first row at the top (`ROWORDER = TOP-DOWN`).
 * The FITS default stores the first row at the bottom.
 */
const isTopDown = (header: FitsHeaderCards): boolean =>
    String(header['ROWORDER'] ?? '').toUpperCase() === 'TOP-DOWN';

/**
 * Turns a point given as fractions of the picture into a FITS pixel position (counted from 1,
 * with the center of the first pixel at 1.0), the way the pixel readout does.
 *
 * @param fraction The point, as fractions of width and height from the top-left corner.
 * @param geometry The image.
 * @returns The FITS pixel position.
 */
export const fractionToFitsPixel = (fraction: Fraction, geometry: ImageGeometry): Point => {
    const turn = geometry.turn ?? 0;
    const shown = turnedSize(geometry.width, geometry.height, turn);
    const topDown = geometry.header ? isTopDown(geometry.header) : false;
    return turnedPictureToFitsPixel(
        { x: fraction.x * shown.w, y: fraction.y * shown.h }, geometry.width, geometry.height, turn, topDown
    );
};

/**
 * The opposite of `fractionToFitsPixel`.
 *
 * @param pixel FITS pixel position.
 * @param geometry The image.
 * @returns The point as fractions of width and height from the top-left corner.
 */
export const fitsPixelToFraction = (pixel: Point, geometry: ImageGeometry): Fraction => {
    const turn = geometry.turn ?? 0;
    const shown = turnedSize(geometry.width, geometry.height, turn);
    const topDown = geometry.header ? isTopDown(geometry.header) : false;
    const point = fitsPixelToTurnedPicture(pixel.x, pixel.y, geometry.width, geometry.height, turn, topDown);
    return { x: point.x / shown.w, y: point.y / shown.h };
};

/**
 * Finds the sky position under a point of an image.
 *
 * @param fraction The point, as fractions of width and height.
 * @param geometry The image.
 * @returns The sky position, or null when the image has no plate solution.
 */
export const skyAtFraction = (fraction: Fraction, geometry: ImageGeometry): SkyPosition | null => {
    const pixelToSky = geometry.header ? createPixelToSky(geometry.header) : null;
    if (!pixelToSky) return null;
    const pixel = fractionToFitsPixel(fraction, geometry);
    return pixelToSky(pixel.x, pixel.y);
};

/**
 * Finds the point of an image that shows a sky position. The sky map only runs from pixel to
 * sky, so this walks toward the answer with Newton steps, using a one-pixel finite difference.
 *
 * @param sky The sky position to find.
 * @param geometry The image, with a plate solution in its header.
 * @returns The point as fractions of width and height, or null when the image has no plate
 *   solution, the walk does not settle, or the position falls outside the image.
 */
export const fractionAtSky = (sky: SkyPosition, geometry: ImageGeometry): Fraction | null => {
    const pixelToSky = geometry.header ? createPixelToSky(geometry.header) : null;
    if (!pixelToSky) return null;
    const cosDec = Math.cos((sky.decDegrees * Math.PI) / 180);
    /** Offset of a pixel from the wanted position, in degrees on the sky (east-west scaled by cos Dec). */
    const offsetAt = (x: number, y: number): Point => {
        const position = pixelToSky(x, y);
        const deltaRa = ((position.raDegrees - sky.raDegrees + 540) % 360) - 180;
        return { x: deltaRa * cosDec, y: position.decDegrees - sky.decDegrees };
    };
    let x = (geometry.width + 1) / 2;
    let y = (geometry.height + 1) / 2;
    for (let step = 0; step < 20; step += 1) {
        const here = offsetAt(x, y);
        const alongX = offsetAt(x + 1, y);
        const alongY = offsetAt(x, y + 1);
        const a = alongX.x - here.x;
        const c = alongX.y - here.y;
        const b = alongY.x - here.x;
        const d = alongY.y - here.y;
        const determinant = a * d - b * c;
        if (!(Math.abs(determinant) > 1e-30)) return null;
        const moveX = (d * here.x - b * here.y) / determinant;
        const moveY = (-c * here.x + a * here.y) / determinant;
        x -= moveX;
        y -= moveY;
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        if (Math.hypot(moveX, moveY) < 1e-3) {
            const fraction = fitsPixelToFraction({ x, y }, geometry);
            const inside = fraction.x >= 0 && fraction.x <= 1 && fraction.y >= 0 && fraction.y <= 1;
            return inside ? fraction : null;
        }
    }
    return null;
};
