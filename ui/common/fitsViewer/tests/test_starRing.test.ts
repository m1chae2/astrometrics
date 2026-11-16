/**
 * @file test_starRing.test.ts
 * @description Tests the "around the star" helper: a ray at a known angle peaks in the right bin, an isotropic
 * star is flat, empty pixels are skipped, the ring limits are respected, and screen directions follow the row order.
 */
import { describe, expect, it } from 'vitest';
import { autoRingLimits, azimuthalProfile, cutoutReach, screenDirection } from '../utils/starRing';
import type { RingSource } from '../utils/starRing';

/**
 * Makes a 41 by 41 cutout centered on pixel (21, 21) whose values come from a function of the offset.
 *
 * @param valueAt Value at an offset (dx along x, dy along y) from the center; null for a missing pixel.
 * @returns The source.
 */
function makeSource(valueAt: (dx: number, dy: number) => number | null): RingSource {
  return {
    center_x: 21,
    center_y: 21,
    cutout_origin_x: 1,
    cutout_origin_y: 1,
    cutout: Array.from({ length: 41 }, (_row, rowIndex) => Array.from({ length: 41 }, (_column, columnIndex) => valueAt(columnIndex - 20, rowIndex - 20))),
  };
}

describe('azimuthalProfile', () => {
  it('puts a ray along +x (90 degrees) in the bin at 90 degrees and nothing 180 degrees away', () => {
    // A ray one pixel wide along +x, from 6 to 18 pixels out.
    const source = makeSource((dx, dy) => (dy === 0 && dx >= 6 ? 100 : 0));
    const profile = azimuthalProfile(source, { innerPx: 5, outerPx: 18 });
    const peakIndex = profile.values.indexOf(Math.max(...profile.values));
    expect(profile.angles[peakIndex]).toBe(92.5);
    const opposite = profile.angles.indexOf(272.5);
    expect(profile.values[opposite]).toBe(0);
  });

  it('measures the angle from +y toward +x: a ray along +y is at 0 degrees', () => {
    const source = makeSource((dx, dy) => (dx === 0 && dy >= 6 ? 100 : 0));
    const profile = azimuthalProfile(source, { innerPx: 5, outerPx: 18 });
    const peakIndex = profile.values.indexOf(Math.max(...profile.values));
    expect(profile.angles[peakIndex]).toBe(2.5);
  });

  it('is flat for an isotropic star', () => {
    const source = makeSource((dx, dy) => 1000 * Math.exp(-(dx * dx + dy * dy) / (2 * 30 * 30)));
    const profile = azimuthalProfile(source, { innerPx: 12, outerPx: 14 });
    expect(profile.values.length).toBeGreaterThan(60);
    // A pixel grid is not perfectly round, so the bins agree within a few percent, not exactly.
    const spread = (Math.max(...profile.values) - Math.min(...profile.values)) / Math.max(...profile.values);
    expect(spread).toBeLessThan(0.1);
  });

  it('skips missing pixels', () => {
    const source = makeSource((dx) => (dx >= 0 ? null : 7));
    const profile = azimuthalProfile(source, { innerPx: 5, outerPx: 15 });
    expect(profile.angles.every((angle) => angle > 180)).toBe(true);
    expect(profile.values.every((value) => value === 7)).toBe(true);
  });

  it('counts the pixels in each bin', () => {
    const source = makeSource(() => 1);
    const profile = azimuthalProfile(source, { innerPx: 2, outerPx: 3 });
    expect(profile.counts).toHaveLength(profile.angles.length);
    expect(Math.max(...profile.counts)).toBeGreaterThan(0);
    expect(profile.counts.reduce((sum, count) => sum + count, 0)).toBeGreaterThanOrEqual(20);
  });

  it('counts only pixels inside the ring limits', () => {
    const source = makeSource((dx, dy) => Math.hypot(dx, dy));
    const profile = azimuthalProfile(source, { innerPx: 10, outerPx: 12 });
    expect(Math.min(...profile.values)).toBeGreaterThanOrEqual(10);
    expect(Math.max(...profile.values)).toBeLessThanOrEqual(12);
  });
});

describe('ring defaults and directions', () => {
  const moffatStar = {
    moffat: { model: 'moffat', fitted: true, fwhm_px: 3.2, peak: 1000, beta: 2.8 },
    gaussian: { model: 'gaussian', fitted: true, fwhm_px: 3.0, peak: 1000 },
    sky_noise: 3.5,
    star_found: true,
  };

  it('puts the inner radius at 1% of the fitted peak and the outer at twice the noise plus 50%', () => {
    // Moffat: alpha = 3.2 / (2 sqrt(2^(1/2.8) - 1)) = 3.02 px, and r = alpha sqrt(ratio^(-1/beta) - 1).
    const alpha = 3.2 / (2 * Math.sqrt(2 ** (1 / 2.8) - 1));
    const radiusAt = (ratio: number) => alpha * Math.sqrt(ratio ** (-1 / 2.8) - 1);
    const limits = autoRingLimits(moffatStar, 20, 40);
    expect(limits.innerPx).toBe(Math.round(radiusAt(0.01)));
    expect(limits.outerPx).toBe(Math.round(1.5 * radiusAt(7 / 1000)));
    expect(limits).toEqual({ innerPx: 6, outerPx: 10 });
  });

  it('uses the Gaussian fit when the Moffat fit failed, and caps the outer radius at the cutout', () => {
    const gaussianOnly = { ...moffatStar, moffat: { model: 'moffat', fitted: false } };
    const sigma = 3.0 / (2 * Math.sqrt(2 * Math.LN2));
    expect(autoRingLimits(gaussianOnly, 20, 40).innerPx).toBe(Math.round(sigma * Math.sqrt(2 * Math.log(100))));
    expect(autoRingLimits({ ...moffatStar, sky_noise: 0.0001 }, 20, 9).outerPx).toBe(9);
  });

  it('falls back to 8 pixels and the cut length without a fit, and keeps the minimum widths', () => {
    expect(autoRingLimits({ ...moffatStar, star_found: false }, 20, 40)).toEqual({ innerPx: 8, outerPx: 20 });
    const noFit = { ...moffatStar, moffat: { model: 'moffat', fitted: false }, gaussian: { model: 'gaussian', fitted: false } };
    expect(autoRingLimits(noFit, 20, 40)).toEqual({ innerPx: 8, outerPx: 20 });
    // A tiny star: the inner radius is at least 2 and the outer at least 4 beyond it.
    const tiny = { ...moffatStar, moffat: { model: 'moffat', fitted: true, fwhm_px: 0.5, peak: 1000, beta: 2.8 } };
    const limits = autoRingLimits(tiny, 20, 40);
    expect(limits.innerPx).toBeGreaterThanOrEqual(2);
    expect(limits.outerPx).toBeGreaterThanOrEqual(limits.innerPx + 4);
    expect(cutoutReach({ cutout: new Array(41).fill([]) })).toBe(20);
  });

  it('turns an angle into the direction it points to on the screen', () => {
    expect([0, 90, 180, 270].map((angle) => screenDirection(angle, true))).toEqual(['Down', 'Right', 'Up', 'Left']);
    expect([0, 90, 180, 270].map((angle) => screenDirection(angle, false))).toEqual(['Up', 'Right', 'Down', 'Left']);
  });
});
