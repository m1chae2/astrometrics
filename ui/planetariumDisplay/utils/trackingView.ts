/**
 * @fileoverview Camera rule for the Planetarium's equatorial tracking mode.
 *
 * In tracking mode the camera follows one point of the sky (an RA/Dec). The sky
 * turns, so that point moves in altitude and azimuth with the clock. If the
 * camera only eases toward it, the camera always trails the clock by a few
 * frames. Every time the clock steps, the stars jump on screen and then glide
 * back. This rule stops that. Once the camera is on the point, it is set to the
 * point's exact altitude and azimuth for the same clock reading the stars are
 * drawn with, so the stars do not move at all. The camera eases only while it
 * travels to a new point.
 */

import { getAltAz } from './projectionMath';
import { easePanStep } from './panEasing';

/** A point of the sky, in degrees. */
export interface SkyAnchor {
  ra: number;
  dec: number;
}

/** What the camera needs to remember between frames while tracking. */
export interface TrackingState {
  /** The point the camera was last locked on, or null if it is not locked. */
  lockedAnchor: SkyAnchor | null;
}

/** The camera direction after one tracking frame. */
export interface TrackingCamera {
  /** Camera altitude, in degrees. */
  altitude: number;
  /** Camera azimuth, in degrees, 0 to 360. */
  azimuth: number;
  /** True while the camera is still travelling to the point. */
  moving: boolean;
}

/** Two anchors closer than this many degrees count as the same point (far below one pixel). */
const SAME_ANCHOR_DEGREES = 1e-9;

/**
 * Tells whether two sky points are the same point.
 *
 * @param {SkyAnchor | null} first - One point, or null.
 * @param {SkyAnchor | null} second - The other point, or null.
 * @returns {boolean} True when both are set and equal.
 */
function sameAnchor(first: SkyAnchor | null, second: SkyAnchor | null): boolean {
  return (
    first !== null &&
    second !== null &&
    Math.abs(first.ra - second.ra) < SAME_ANCHOR_DEGREES &&
    Math.abs(first.dec - second.dec) < SAME_ANCHOR_DEGREES
  );
}

/**
 * Works out where the camera points for one frame of equatorial tracking.
 *
 * @param {SkyAnchor} anchor - The RA/Dec being followed.
 * @param {number} lst - Local sidereal time of this frame, in degrees. Use the same value to draw the stars.
 * @param {number} observerLat - Observer latitude, in degrees.
 * @param {{ altitude: number; azimuth: number }} camera - Where the camera pointed last frame.
 * @param {number} pixelsPerDegree - Screen pixels per degree at the current zoom.
 * @param {TrackingState} state - Memory kept between frames. Updated in place.
 * @returns {TrackingCamera} The camera direction for this frame.
 */
export function trackingCameraStep(
  anchor: SkyAnchor,
  lst: number,
  observerLat: number,
  camera: { altitude: number; azimuth: number },
  pixelsPerDegree: number,
  state: TrackingState,
): TrackingCamera {
  const target = getAltAz(anchor.ra, anchor.dec, lst, observerLat);
  if (sameAnchor(state.lockedAnchor, anchor)) {
    return { altitude: target.alt, azimuth: target.az, moving: false };
  }
  // A new point: travel there, then lock on.
  let deltaAzimuth = target.az - camera.azimuth;
  if (deltaAzimuth > 180) deltaAzimuth -= 360;
  if (deltaAzimuth < -180) deltaAzimuth += 360;
  const step = easePanStep(target.alt - camera.altitude, deltaAzimuth, pixelsPerDegree);
  if (!step.moving) {
    state.lockedAnchor = { ra: anchor.ra, dec: anchor.dec };
    return { altitude: target.alt, azimuth: target.az, moving: false };
  }
  return {
    altitude: camera.altitude + step.altitudeStep,
    azimuth: (camera.azimuth + step.azimuthStep + 360) % 360,
    moving: true,
  };
}
