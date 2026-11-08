/**
 * @fileoverview Checks that the sky turns smoothly with the clock and that drawing is skipped
 * when the sky has moved less than the pixel tolerance.
 */

import { describe, expect, it } from 'vitest';
import { getAltAz, pixelsPerDegree, projectAltAz } from '../utils/projectionMath';
import { calculateLST } from '../utils/siderealTime';
import { SKY_MOTION_REDRAW_PIXELS, skyMovedEnoughToDraw } from '../utils/skyMotion';

const LATITUDE = 45.76;
const LONGITUDE = -110.74;
const WIDTH = 1000;
const HEIGHT = 600;
const FRAME_MS = 1000 / 60;
const START_MS = Date.UTC(2026, 10, 6, 6, 0, 0);

/** Screen position of a star at the given clock reading, with the camera fixed. */
function starScreenPosition(clockMs: number, fov: number) {
  const lst = calculateLST(new Date(clockMs), 0, LONGITUDE);
  const { alt, az } = getAltAz(349.94, 61.85, lst, LATITUDE);
  return projectAltAz(alt, az, 50, 20, fov, WIDTH, HEIGHT);
}

describe('sky motion', () => {
  /** With the clock read every frame, a star moves by a small, even step each frame. */
  it('moves a star smoothly from frame to frame at a tight field of view', () => {
    const steps: number[] = [];
    let previous = starScreenPosition(START_MS, 0.1);
    for (let frame = 1; frame <= 180; frame += 1) {
      const next = starScreenPosition(START_MS + frame * FRAME_MS, 0.1);
      steps.push(Math.hypot(next.x - previous.x, next.y - previous.y));
      previous = next;
    }
    const meanStep = steps.reduce((total, step) => total + step, 0) / steps.length;
    expect(meanStep).toBeGreaterThan(0);
    // No frame jumps more than a few percent away from the average step.
    for (const step of steps) expect(Math.abs(step - meanStep)).toBeLessThan(meanStep * 0.05);
    // The old once-a-second clock would have moved the star by the whole second at once.
    const oneSecondStep = Math.hypot(
      starScreenPosition(START_MS + 1000, 0.1).x - starScreenPosition(START_MS, 0.1).x,
      starScreenPosition(START_MS + 1000, 0.1).y - starScreenPosition(START_MS, 0.1).y,
    );
    expect(oneSecondStep).toBeGreaterThan(meanStep * 30);
  });

  /** A wide view skips draws while the sky moves under the tolerance. */
  it('skips the draw when the sky moved less than the tolerance', () => {
    const lastDrawnLst = 100;
    const ppd = pixelsPerDegree(WIDTH, HEIGHT, 60);
    const tinyTurn = (SKY_MOTION_REDRAW_PIXELS * 0.5) / ppd;
    const bigTurn = (SKY_MOTION_REDRAW_PIXELS * 2) / ppd;
    expect(skyMovedEnoughToDraw(lastDrawnLst + tinyTurn, lastDrawnLst, ppd)).toBe(false);
    expect(skyMovedEnoughToDraw(lastDrawnLst + bigTurn, lastDrawnLst, ppd)).toBe(true);
    expect(skyMovedEnoughToDraw(lastDrawnLst, null, ppd)).toBe(true);
  });

  /** The tolerance works across the 360 to 0 degree wrap. */
  it('handles the wrap of sidereal time', () => {
    expect(skyMovedEnoughToDraw(0.0001, 359.9999, 1000)).toBe(false);
    expect(skyMovedEnoughToDraw(0.01, 359.99, 1000)).toBe(true);
  });

  /** Draw rate: a tight view draws most frames, a wide view only every few seconds. */
  it('draws often at 0.1 degrees and rarely at 60 degrees', () => {
    /** Counts draws over ten seconds of 60 Hz frames. */
    function drawsIn(fov: number): number {
      const ppd = pixelsPerDegree(WIDTH, HEIGHT, fov);
      let lastDrawn: number | null = null;
      let draws = 0;
      for (let frame = 0; frame < 600; frame += 1) {
        const lst = calculateLST(new Date(START_MS + frame * FRAME_MS), 0, LONGITUDE);
        if (skyMovedEnoughToDraw(lst, lastDrawn, ppd)) {
          draws += 1;
          lastDrawn = lst;
        }
      }
      return draws;
    }
    expect(drawsIn(0.1)).toBeGreaterThan(150);
    expect(drawsIn(60)).toBeLessThanOrEqual(3);
  });
});
