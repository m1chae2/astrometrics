/**
 * @file starRing.ts
 * @description Helpers for the "around the star" view. The library measures the ring (its radii, the brightness
 * by direction, the neighbors left out and the Fourier terms); this file only turns those numbers into what the
 * panel draws and says: the smooth curve of the significant terms, the one-line summary, and the screen
 * direction of an angle.
 *
 * The direction uses the same convention as the cut: degrees from +y (up a column of the image) toward +x, from
 * 0 up to 360.
 */
import type { RingHarmonic, StarProfileMeasurement } from '../../types/backendTypes';
import { turnStep } from '../displayTurn';

/** Fewest pixels a direction bin should hold for its mean to be steady. */
export const FEW_PIXELS_PER_BIN = 4;

/** The most terms the one-line summary names. */
export const SUMMARY_TERM_COUNT = 3;

/** The summary when no term reaches the library's significance threshold. */
export const ROUND_WITHIN_NOISE = 'Round within the noise';

/** The pieces of a measurement that the ring plot needs. */
export type RingSource = Pick<
  StarProfileMeasurement,
  'ring_bin_angle_degrees' | 'ring_bin_counts' | 'ring_harmonics' | 'ring_constant' | 'ring_scale_value'
>;

/** A smooth curve of brightness against direction. */
export interface RingCurve {
  /** Directions, in degrees from 0 to 360. */
  angles: number[];
  /** Brightness above the sky at each direction. */
  values: number[];
}

/**
 * Names the pattern that a Fourier order describes.
 *
 * @param order The number of lobes.
 * @returns "One-sided", "Oval", "Three-fold", "Four-fold", or "m-fold" for a higher order.
 */
export function orderName(order: number): string {
  if (order === 1) return 'One-sided';
  if (order === 2) return 'Oval';
  if (order === 3) return 'Three-fold';
  if (order === 4) return 'Four-fold';
  return `${order}-fold`;
}

/**
 * Writes a fraction as a percent, with a decimal when it is under 10%.
 *
 * @param fraction The fraction, for example 0.124.
 * @returns The text, for example "12%" or "3.4%".
 */
export function formatPercent(fraction: number): string {
  const percent = fraction * 100;
  return `${Math.abs(percent) < 9.95 ? percent.toFixed(1) : percent.toFixed(0)}%`;
}

/**
 * Names the screen direction that an angle points to, for the axis ticks.
 *
 * Image x runs to the right, and image y runs down the screen when the file's first row is at the top,
 * and up the screen otherwise, as long as the picture is not turned. When the picture is shown turned
 * by a quarter turn, the directions turn with it. So with no turn, 0 degrees points down or up, and
 * 90 degrees points right.
 *
 * @param angleDegrees One of 0, 90, 180 or 270 (any other angle is rounded to the nearest of them).
 * @param firstRowAtTop True when the picture shows the file's first row at the top.
 * @param turn How far the picture is shown turned, clockwise in degrees (default 0).
 * @returns "Up", "Right", "Down" or "Left".
 */
export function screenDirection(
  angleDegrees: number, firstRowAtTop: boolean, turn: number = 0,
): 'Up' | 'Right' | 'Down' | 'Left' {
  const quarter = (((Math.round(angleDegrees / 90) % 4) + 4) % 4);
  // The direction as a step in the file's own x and y, then in the base picture, then in the turned one.
  const fileStepX = quarter === 1 ? 1 : quarter === 3 ? -1 : 0;
  const fileStepY = quarter === 0 ? 1 : quarter === 2 ? -1 : 0;
  const shown = turnStep(fileStepX, firstRowAtTop ? fileStepY : -fileStepY, turn);
  if (shown.x > 0.5) return 'Right';
  if (shown.x < -0.5) return 'Left';
  return shown.y > 0 ? 'Down' : 'Up';
}

/**
 * Writes the one-line summary of the ring: the strongest significant terms, biggest first.
 *
 * The amplitudes are the ones with the noise bias removed, as a percent of the ring's mean brightness, with their
 * 1 sigma uncertainty. Only the one-sided term names a direction, taken from the nearest screen axis.
 *
 * @param harmonics The library's Fourier terms.
 * @param firstRowAtTop True when the picture shows the file's first row at the top.
 * @param turn How far the picture is shown turned, clockwise in degrees (default 0).
 * @returns For example "One-sided 12% ± 2% toward Up; oval 3% ± 1%", or "Round within the noise".
 */
export function describeRing(harmonics: RingHarmonic[], firstRowAtTop: boolean, turn: number = 0): string {
  const strongest = harmonics
    .filter((term) => term.significant)
    .sort((first, second) => second.amplitude_debiased - first.amplitude_debiased)
    .slice(0, SUMMARY_TERM_COUNT);
  if (strongest.length === 0) return ROUND_WITHIN_NOISE;
  return strongest
    .map((term, index) => {
      const name = index === 0 ? orderName(term.order) : orderName(term.order).toLowerCase();
      const size = `${name} ${formatPercent(term.amplitude_debiased)} ± ${formatPercent(term.amplitude_error)}`;
      return term.order === 1 ? `${size} toward ${screenDirection(term.direction_degrees, firstRowAtTop, turn)}` : size;
    })
    .join('; ');
}

/**
 * Builds the smooth curve of the constant term plus every significant Fourier term, in brightness units.
 *
 * Each term adds `amplitude * cos(order * (angle - direction))` to a constant of 1; the sum is scaled by the
 * constant term and the mean brightness the library fitted at the ring's radii.
 *
 * @param source The measurement's ring fields.
 * @param stepDegrees The spacing of the curve's points, in degrees.
 * @returns The curve, or null when the ring was not analyzed.
 */
export function harmonicCurve(source: RingSource, stepDegrees = 2): RingCurve | null {
  const { ring_constant: constant, ring_scale_value: scale } = source;
  if (constant === null || constant === undefined || scale === null || scale === undefined) return null;
  if (source.ring_harmonics.length === 0) return null;
  const terms = source.ring_harmonics.filter((term) => term.significant);
  const angles: number[] = [];
  const values: number[] = [];
  for (let angle = 0; angle <= 360; angle += stepDegrees) {
    let relative = 1;
    for (const term of terms) {
      relative += term.amplitude * Math.cos((term.order * (angle - term.direction_degrees) * Math.PI) / 180);
    }
    angles.push(angle);
    values.push(scale * constant * relative);
  }
  return { angles, values };
}

/**
 * Says whether some directions of the ring hold too few pixels, or none at all.
 *
 * @param counts The pixel count of each bin the library returned (empty bins are not returned).
 * @param angles The middle angle of each of those bins.
 * @returns True when a bin has fewer than the steady count, or a bin is missing.
 */
export function hasFewPixels(counts: number[], angles: number[]): boolean {
  if (counts.length === 0) return true;
  let width = 360;
  for (let index = 1; index < angles.length; index += 1) width = Math.min(width, angles[index] - angles[index - 1]);
  const expectedBins = Math.round(360 / Math.max(width, 1));
  return counts.some((count) => count < FEW_PIXELS_PER_BIN) || counts.length < expectedBins;
}
