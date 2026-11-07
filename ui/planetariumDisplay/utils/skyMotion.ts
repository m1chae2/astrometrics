/**
 * @fileoverview Rule for when the sky map must draw again because the sky has turned.
 *
 * The map reads the clock on every animation frame, so the sky turns smoothly. Drawing is
 * the costly part, so the map draws again only when the turning since the last draw has
 * moved the sky by more than a small distance on screen. A wide view then idles, and a
 * tight view (0.1 degrees across) draws often enough to look smooth.
 */

/** How far (screen pixels) the sky may turn before the map draws again. Under a third of a pixel. */
export const SKY_MOTION_REDRAW_PIXELS = 0.25;

/**
 * Tells how far the sky has turned between two sidereal times.
 *
 * @param {number} lst - Local sidereal time now, in degrees.
 * @param {number} lastDrawnLst - Local sidereal time at the last draw, in degrees.
 * @returns {number} The turn in degrees, from 0 to 180.
 */
export function skyTurnDegrees(lst: number, lastDrawnLst: number): number {
  const difference = Math.abs(lst - lastDrawnLst);
  return difference > 180 ? 360 - difference : difference;
}

/**
 * Tells whether the sky has turned enough since the last draw to be worth a new draw.
 *
 * @param {number} lst - Local sidereal time now, in degrees.
 * @param {number | null} lastDrawnLst - Local sidereal time at the last draw, or null if none yet.
 * @param {number} pixelsPerDegree - Screen pixels per degree at the current zoom.
 * @returns {boolean} True when the sky moved by more than SKY_MOTION_REDRAW_PIXELS.
 */
export function skyMovedEnoughToDraw(lst: number, lastDrawnLst: number | null, pixelsPerDegree: number): boolean {
  if (lastDrawnLst === null) return true;
  return skyTurnDegrees(lst, lastDrawnLst) * pixelsPerDegree > SKY_MOTION_REDRAW_PIXELS;
}
