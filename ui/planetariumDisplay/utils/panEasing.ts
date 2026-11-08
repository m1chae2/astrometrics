/**
 * @fileoverview Pan easing for the Planetarium sky map.
 *
 * Dragging or pressing an arrow key only moves a pan target. The render loop then
 * moves the camera a share of the remaining distance each frame. The camera snaps
 * onto the target once it is less than a fraction of a screen pixel away, so the
 * easing ends and the map stops redrawing.
 */

/** Share of the remaining pan distance covered each frame (about 5 frames to close half the gap). */
const PAN_EASE_FRACTION = 0.15;

/** The camera snaps onto its pan target when it is closer than this many screen pixels. */
export const PAN_SETTLE_PIXELS = 0.25;

/**
 * Moves the camera one frame toward its pan target, or snaps it on when close enough.
 *
 * @param {number} deltaAltitude - Target altitude minus camera altitude, in degrees.
 * @param {number} deltaAzimuth - Target azimuth minus camera azimuth, in degrees, wrapped to -180..180.
 * @param {number} pixelsPerDegree - Screen pixels per degree at the current zoom.
 * @returns {{ altitudeStep: number; azimuthStep: number; moving: boolean }} The change to add to
 * the camera this frame, and whether the camera is still moving. When `moving` is false the steps
 * are the whole remaining distance, so adding them puts the camera exactly on the target.
 */
export function easePanStep(
  deltaAltitude: number,
  deltaAzimuth: number,
  pixelsPerDegree: number,
): { altitudeStep: number; azimuthStep: number; moving: boolean } {
  const settleDegrees = PAN_SETTLE_PIXELS / Math.max(pixelsPerDegree, 1e-9);
  if (Math.abs(deltaAltitude) <= settleDegrees && Math.abs(deltaAzimuth) <= settleDegrees) {
    return { altitudeStep: deltaAltitude, azimuthStep: deltaAzimuth, moving: false };
  }
  return {
    altitudeStep: deltaAltitude * PAN_EASE_FRACTION,
    azimuthStep: deltaAzimuth * PAN_EASE_FRACTION,
    moving: true,
  };
}
