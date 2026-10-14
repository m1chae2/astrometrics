/**
 * @module chartScale
 * @fileoverview Shared "round up to a clean bound" scaling helpers for small inline
 * SVG charts (e.g. AlignmentPointCard's polar dispersion and time-series panels),
 * so a chart's axis/ring bounds land on human-friendly numbers instead of the raw
 * padded peak value.
 */

/**
 * Rounds a padded peak magnitude up to a clean decimal bound appropriate to its
 * scale, so an axis maximum reads as e.g. 0.05, 0.5, 2.0, or 15 rather than an
 * arbitrary value like 1.2345.
 *
 * @param {number} paddedValue - The peak value already padded (e.g. by 25%).
 * @returns {number} A clean rounded-up bound.
 */
export function roundUpToCleanBound(paddedValue: number): number {
  if (paddedValue < 0.2) {
    return Math.ceil(paddedValue * 100) / 100;
  } else if (paddedValue < 1.0) {
    return Math.ceil(paddedValue * 20) / 20;
  } else if (paddedValue < 10.0) {
    return Math.ceil(paddedValue * 2) / 2;
  }
  return Math.ceil(paddedValue / 5) * 5;
}

/**
 * Computes a chart axis/radius maximum from a peak error value, padded by a
 * fixed margin and floored at a minimum scale so near-zero jitter still
 * renders a legible chart.
 *
 * @param {number} peakError - The largest observed deviation, in the chart's units.
 * @param {number} [minimumBound=0.04] - The smallest allowed axis bound.
 * @param {number} [paddingFactor=1.25] - Multiplier applied to peakError before rounding.
 * @returns {number} The clean axis/radius maximum.
 */
export function computeChartMaxBound(
  peakError: number,
  minimumBound = 0.04,
  paddingFactor = 1.25,
): number {
  return roundUpToCleanBound(Math.max(minimumBound, peakError * paddingFactor));
}

/**
 * Picks a clean concentric-ring step size so a chart shows 2-3 rings
 * regardless of the axis maximum's magnitude.
 *
 * @param {number} maxBound - The chart's axis/radius maximum.
 * @param {number} [divisions=3] - Target number of ring intervals across maxBound.
 * @returns {number} A clean ring step size.
 */
export function computeRingStep(maxBound: number, divisions = 3): number {
  const rawStep = maxBound / divisions;
  if (rawStep < 0.05) return 0.02;
  if (rawStep < 0.15) return 0.05;
  if (rawStep < 0.35) return 0.2;
  if (rawStep < 0.75) return 0.5;
  if (rawStep < 2.5) return 1.0;
  if (rawStep < 7.5) return 5.0;
  if (rawStep < 25.0) return 10.0;
  if (rawStep < 75.0) return 50.0;
  return Math.ceil(rawStep / 50) * 50;
}
