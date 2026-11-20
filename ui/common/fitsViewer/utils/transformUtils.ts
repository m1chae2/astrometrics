/**
 * @file transformUtils.ts
 * @description Small helpers for the Fits Viewer's pan and zoom math, including the CSS
 * transform that lets an overlay drawn for an earlier view follow the image.
 */

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
