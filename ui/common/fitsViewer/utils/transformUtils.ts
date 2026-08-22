/**
 * Utility functions for Fits Viewer coordinate transforms.
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
