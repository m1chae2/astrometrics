/**
 * @file starRing.ts
 * @description The "around the star" view: how bright the pixels are in a ring around the star's center, by
 * direction. A spike or a flare shows as a peak at its direction. The ring is cut from the cutout that comes
 * with each measurement, so no call to the backend is needed.
 *
 * The direction uses the same convention as the cut: degrees from +y (up a column of the image) toward +x,
 * from 0 up to 360.
 */
import type { CutSource } from './starCut';

/** Width of one direction bin, in degrees. */
export const RING_BIN_DEGREES = 5;

/** The pieces of a measurement that the ring needs. */
export type RingSource = Pick<CutSource, 'center_x' | 'center_y' | 'cutout' | 'cutout_origin_x' | 'cutout_origin_y'>;

/** The ring's brightness by direction. Only bins that hold at least one pixel appear. */
export interface RingProfile {
  /** Middle of each bin, in degrees from 0 to 360. */
  angles: number[];
  /** Mean value above the sky of the pixels in each bin. */
  values: number[];
  /** How many pixels went into each bin. */
  counts: number[];
}

/** The ring's two radii, in pixels. */
export interface RingLimits {
  /** Inner radius. */
  innerPx: number;
  /** Outer radius. */
  outerPx: number;
}

/**
 * Finds how far the cutout reaches from its middle, so a ring cannot ask for pixels the cutout lacks.
 *
 * @param source The measurement.
 * @returns The reach, in pixels.
 */
export function cutoutReach(source: Pick<CutSource, 'cutout'>): number {
  return Math.max(1, (source.cutout.length - 1) / 2);
}

/** Fewest pixels a direction bin should hold for its mean to be steady. */
export const FEW_PIXELS_PER_BIN = 4;

/** The fit's numbers that the automatic ring needs. */
export interface RingFit {
  /** "moffat" or "gaussian". */
  model: string;
  /** Whether the fit worked. */
  fitted: boolean;
  /** Width at half the peak, in pixels. */
  fwhm_px?: number | null;
  /** Height of the fitted profile at the center. */
  peak?: number | null;
  /** Shape of the Moffat wings. */
  beta?: number | null;
}

/** The star's fits and sky noise, for the automatic ring. */
export interface AutoRingSource {
  /** The Moffat fit. */
  moffat: RingFit;
  /** The Gaussian fit. */
  gaussian: RingFit;
  /** Scatter of the sky, in the same units as the profile. */
  sky_noise: number;
  /** Whether the profile looks like a star. */
  star_found: boolean;
}

/**
 * Finds the radius at which the fitted profile has fallen to a level.
 *
 * @param fit A fit that worked.
 * @param level The level, in the profile's units.
 * @returns The radius in pixels, or null when the fit cannot say (no width, no peak, or a level at or above the peak).
 */
function radiusAtLevel(fit: RingFit, level: number): number | null {
  const { fwhm_px: width, peak } = fit;
  if (!fit.fitted || !width || !peak || peak <= 0 || level <= 0 || level >= peak) return null;
  const ratio = level / peak;
  if (fit.model === 'moffat' && fit.beta && fit.beta > 0) {
    // I(r) = peak (1 + (r / alpha)^2)^-beta, with FWHM = 2 alpha sqrt(2^(1/beta) - 1).
    const alpha = width / (2 * Math.sqrt(2 ** (1 / fit.beta) - 1));
    return alpha * Math.sqrt(ratio ** (-1 / fit.beta) - 1);
  }
  if (fit.model === 'gaussian') {
    const sigma = width / (2 * Math.sqrt(2 * Math.LN2));
    return sigma * Math.sqrt(-2 * Math.log(ratio));
  }
  return null;
}

/**
 * Sizes the ring to the star: the inner radius is where the fitted profile has fallen to 1% of its peak (so the
 * noisy core is left out), and the outer radius is where it has fallen to twice the sky noise, plus 50%. Whole
 * pixels, the inner at least 2, the outer at least 4 more, and never past the cutout. A profile with no usable fit
 * gets 8 pixels and the cut length.
 *
 * @param source The star's fits and sky noise.
 * @param cutLengthPx The cut length, for the fallback.
 * @param reachPx How far the cutout reaches.
 * @returns The two radii.
 */
export function autoRingLimits(source: AutoRingSource, cutLengthPx: number, reachPx: number): RingLimits {
  const fit = source.star_found ? [source.moffat, source.gaussian].find((candidate) => radiusAtLevel(candidate, 0.01 * (candidate.peak ?? 0)) !== null) : undefined;
  let innerPx = 8;
  let outerPx = cutLengthPx;
  if (fit && fit.peak) {
    innerPx = radiusAtLevel(fit, 0.01 * fit.peak) ?? innerPx;
    const noiseRadius = radiusAtLevel(fit, 2 * source.sky_noise);
    // When the fit never falls to twice the noise (or the noise is not known), the cut length is the far limit.
    outerPx = noiseRadius !== null ? 1.5 * noiseRadius : cutLengthPx;
  }
  const reach = Math.floor(reachPx);
  const inner = Math.min(Math.max(Math.round(innerPx), 2), Math.max(2, reach - 4));
  // The outer radius is pushed out to keep a band 4 pixels wide, and held at the edge of the cutout.
  const outer = Math.min(Math.max(Math.round(outerPx), inner + 4), Math.max(reach, inner + 4));
  return { innerPx: inner, outerPx: outer };
}

/**
 * Averages the cutout's pixels by direction around the center, for pixels in the ring.
 *
 * @param source The measurement, with its cutout.
 * @param limits The ring's radii. A pixel at exactly a radius counts.
 * @returns The mean value in each 5 degree bin that holds a pixel.
 */
export function azimuthalProfile(source: RingSource, limits: RingLimits): RingProfile {
  const binCount = 360 / RING_BIN_DEGREES;
  const sums = new Array<number>(binCount).fill(0);
  const counts = new Array<number>(binCount).fill(0);
  source.cutout.forEach((row, rowIndex) => {
    row.forEach((value, columnIndex) => {
      if (value === null || !Number.isFinite(value)) return;
      const dx = source.cutout_origin_x + columnIndex - source.center_x;
      const dy = source.cutout_origin_y + rowIndex - source.center_y;
      const radius = Math.hypot(dx, dy);
      if (radius < limits.innerPx || radius > limits.outerPx) return;
      // The same angle as the cut: sin(angle) along x and cos(angle) along y.
      const degrees = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
      const bin = Math.min(binCount - 1, Math.floor(degrees / RING_BIN_DEGREES));
      sums[bin] += value;
      counts[bin] += 1;
    });
  });
  const profile: RingProfile = { angles: [], values: [], counts: [] };
  counts.forEach((count, bin) => {
    if (count === 0) return;
    profile.angles.push((bin + 0.5) * RING_BIN_DEGREES);
    profile.values.push(sums[bin] / count);
    profile.counts.push(count);
  });
  return profile;
}

/**
 * Names the screen direction that an angle points to, for the axis ticks.
 *
 * Image x always runs to the right. Image y runs down the screen when the file's first row is at the top,
 * and up the screen otherwise. So 0 degrees points down or up, and 90 degrees points right.
 *
 * @param angleDegrees One of 0, 90, 180 or 270.
 * @param firstRowAtTop True when the picture shows the file's first row at the top.
 * @returns "Up", "Right", "Down" or "Left".
 */
export function screenDirection(angleDegrees: number, firstRowAtTop: boolean): 'Up' | 'Right' | 'Down' | 'Left' {
  const quarter = (((Math.round(angleDegrees / 90) % 4) + 4) % 4);
  if (quarter === 1) return 'Right';
  if (quarter === 3) return 'Left';
  const alongY = quarter === 0;
  return alongY === firstRowAtTop ? 'Down' : 'Up';
}
