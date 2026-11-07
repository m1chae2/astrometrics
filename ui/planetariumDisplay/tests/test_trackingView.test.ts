/**
 * @fileoverview Tests for the equatorial tracking camera rule: with the camera
 * locked on a fixed RA/Dec, stars do not move on screen as the clock advances,
 * and a new RA/Dec moves the camera there smoothly.
 */

import { describe, it, expect } from 'vitest';
import { trackingCameraStep, TrackingState } from '../utils/trackingView';
import { getAltAz, projectAltAz, pixelsPerDegree } from '../utils/projectionMath';

const LATITUDE = 45.76;
const WIDTH = 1500;
const HEIGHT = 900;
const FOV = 2;
const PPD = pixelsPerDegree(WIDTH, HEIGHT, FOV);
const ANCHOR = { ra: 250.24, dec: 36.51 };
const STARS = [
  { ra: 250.3, dec: 36.6 },
  { ra: 250.1, dec: 36.4 },
  { ra: 250.5, dec: 36.55 },
];

/**
 * Screen positions of the test stars for one clock reading and camera.
 *
 * @param {number} lst - Sidereal time in degrees.
 * @param {{ altitude: number; azimuth: number }} camera - The camera direction.
 * @returns {Array<{ x: number; y: number }>} Pixel positions.
 */
function screenPositions(lst: number, camera: { altitude: number; azimuth: number }) {
  return STARS.map((star) => {
    const { alt, az } = getAltAz(star.ra, star.dec, lst, LATITUDE);
    const p = projectAltAz(alt, az, camera.altitude, camera.azimuth, FOV, WIDTH, HEIGHT);
    return { x: p.x, y: p.y };
  });
}

/**
 * Runs frames until the camera locks on the anchor.
 *
 * @param {TrackingState} state - Tracking memory.
 * @param {number} lst - Starting sidereal time.
 * @returns {{ camera: { altitude: number; azimuth: number }; lst: number }} Settled camera and time.
 */
function settle(state: TrackingState, lst: number) {
  let camera = { altitude: 10, azimuth: 100 };
  let time = lst;
  for (let i = 0; i < 500; i += 1) {
    const next = trackingCameraStep(ANCHOR, time, LATITUDE, camera, PPD, state);
    camera = { altitude: next.altitude, azimuth: next.azimuth };
    time += 0.0002;
    if (!next.moving) break;
  }
  return { camera, lst: time };
}

describe('trackingCameraStep', () => {
  it('keeps star screen positions fixed while the clock advances', () => {
    const state: TrackingState = { lockedAnchor: null };
    const settled = settle(state, 120);
    let lst = settled.lst;
    let camera = settled.camera;
    const first = trackingCameraStep(ANCHOR, lst, LATITUDE, camera, PPD, state);
    camera = { altitude: first.altitude, azimuth: first.azimuth };
    let previous = screenPositions(lst, camera);
    for (let step = 0; step < 600; step += 1) {
      lst += 0.004167; // one second of sidereal rotation
      const next = trackingCameraStep(ANCHOR, lst, LATITUDE, camera, PPD, state);
      camera = { altitude: next.altitude, azimuth: next.azimuth };
      const current = screenPositions(lst, camera);
      // Frame to frame, nothing jumps. The slow turn of the alt-az frame (field rotation) is
      // far below a pixel per second.
      current.forEach((position, index) => {
        expect(Math.abs(position.x - previous[index].x)).toBeLessThan(0.01);
        expect(Math.abs(position.y - previous[index].y)).toBeLessThan(0.01);
      });
      previous = current;
    }
  });

  it('does not move when the same position arrives again', () => {
    const state: TrackingState = { lockedAnchor: null };
    const settled = settle(state, 50);
    const again = trackingCameraStep({ ...ANCHOR }, settled.lst, LATITUDE, settled.camera, PPD, state);
    const target = getAltAz(ANCHOR.ra, ANCHOR.dec, settled.lst, LATITUDE);
    expect(again.moving).toBe(false);
    expect(again.altitude).toBeCloseTo(target.alt, 9);
  });

  it('eases to a changed position and then locks on it', () => {
    const state: TrackingState = { lockedAnchor: null };
    const settled = settle(state, 50);
    const moved = { ra: ANCHOR.ra + 1, dec: ANCHOR.dec + 1 };
    const firstStep = trackingCameraStep(moved, settled.lst, LATITUDE, settled.camera, PPD, state);
    const target = getAltAz(moved.ra, moved.dec, settled.lst, LATITUDE);
    expect(firstStep.moving).toBe(true);
    expect(Math.abs(firstStep.altitude - target.alt)).toBeLessThan(Math.abs(settled.camera.altitude - target.alt));
    let camera = { altitude: firstStep.altitude, azimuth: firstStep.azimuth };
    let result = firstStep;
    for (let i = 0; i < 300 && result.moving; i += 1) {
      result = trackingCameraStep(moved, settled.lst, LATITUDE, camera, PPD, state);
      camera = { altitude: result.altitude, azimuth: result.azimuth };
    }
    expect(result.moving).toBe(false);
    expect(state.lockedAnchor).toEqual(moved);
  });
});
