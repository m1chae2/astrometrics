/**
 * @file skyRange.ts
 * @description The black and white points of the viewer's Linear display, set from the sky's noise.
 *
 * Zscale sets the range from all the pixels, so it changes with what fills the frame: a big galaxy
 * widens it and an empty field narrows it. That makes equal noise look unequally grainy. This
 * module sets the range from the sky alone. It finds the sky level (the median) and the noise (the
 * spread of the sky pixels, measured from differences of pixels a few columns apart so smooth light cancels) after throwing out stars and other bright things, then puts black a
 * fixed number of noise steps below the sky and white a fixed number above it.
 * Equal sky noise then looks equally grainy on any image.
 *
 * Reference implementation: `measure_sky_background` in
 * `astrometricslib/pipelines/shared/image_scaling.py`, the library's one sky measurement. This
 * module is its port (same clipping, same neighbor-difference noise); keep the two in step.
 * `test_skyRange.test.ts` checks that both give the same numbers on one fixed image.
 */
import { computeZscaleRange, ZSCALE_SAMPLE_COUNT } from './zscale';

/** Black is set this many noise steps (sigmas) below the sky level. */
export const SKY_RANGE_BLACK_SIGMAS = -2;

/** White is set this many noise steps (sigmas) above the sky level. */
export const SKY_RANGE_WHITE_SIGMAS = 10;

/** A pixel further than this many sigmas from the sky is left out of the sky statistics. */
export const SKY_CLIP_SIGMAS = 3;

/** The clipping is repeated at most this many times. */
const MAXIMUM_CLIP_ITERATIONS = 5;

/** Fewer sample pixels than this are too few to measure a noise level. */
const MINIMUM_SKY_PIXELS = 25;

/** Converts a median absolute deviation into a standard deviation for Gaussian noise. */
const MAD_TO_SIGMA = 1.4826;

/**
 * The spread of the difference of two independent noisy pixels is this many times the spread of
 * one pixel (the square root of 2), because their variances add.
 */
const DIFFERENCE_NOISE_FACTOR = Math.SQRT2;

/**
 * How many columns apart the two pixels of a noise difference are.
 *
 * Registering a stack resamples its frames, which blends neighboring pixels and makes their noise
 * partly alike. Two pixels that are too close then cancel too much, and the noise reads too low.
 * Measured on the M 31 and NGC 7331 L stacks on 2026-11-20, the noise grew with spacing and
 * settled from about 3 px (M 31: 3.56e-5 at 1 px, 4.28e-5 at 3 px, 4.42e-5 at 8 px). The two
 * pixels must be far enough apart to be independent, but close enough that smooth light (a galaxy
 * or a gradient) still cancels, so this is the first spacing past the settling point, with one to spare.
 */
export const NEIGHBOR_DIFFERENCE_SPACING_PX = 4;

/**
 * Finds the median of a list of values.
 *
 * @param values Values in rising order; not empty.
 * @returns The middle value, or the mean of the two middle values.
 */
function medianOfSorted(values: Float64Array): number {
  const middle = Math.floor(values.length / 2);
  return values.length % 2 === 1 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
}

/**
 * Finds the middle value and spread of a list of values with sigma clipping.
 *
 * Each round takes the median as the middle and the median absolute deviation (scaled to a standard
 * deviation) as the spread, then drops the values further than the clip limit from the middle.
 * It stops when nothing more is dropped.
 *
 * @param values Values to measure, all finite.
 * @returns The middle value and spread, or null when there are too few values or the spread is not above zero.
 */
function clippedMedianAndSigma(values: ArrayLike<number>): { middle: number; sigma: number } | null {
  let kept = Float64Array.from(values).sort();
  let middle = 0;
  let sigma = 0;
  for (let iteration = 0; iteration < MAXIMUM_CLIP_ITERATIONS; iteration++) {
    if (kept.length < MINIMUM_SKY_PIXELS) return null;
    middle = medianOfSorted(kept);
    const deviations = Float64Array.from(kept, (value) => Math.abs(value - middle)).sort();
    sigma = MAD_TO_SIGMA * medianOfSorted(deviations);
    if (!(sigma > 0) || !Number.isFinite(sigma)) return null;
    const limit = SKY_CLIP_SIGMAS * sigma;
    const clipped = kept.filter((value) => Math.abs(value - middle) <= limit);
    if (clipped.length === kept.length) break;
    kept = clipped;
  }
  if (kept.length < MINIMUM_SKY_PIXELS) return null;
  return { middle, sigma };
}

/**
 * Samples the sky pixels and the differences to the pixels a few columns to their right.
 *
 * It visits the grid positions of `sampleImageGrid`, but only those that have a partner pixel
 * `NEIGHBOR_DIFFERENCE_SPACING_PX` columns away, and only when both pixels are finite. This is the
 * same choice `measure_sky_background` makes in the library: the last columns, which have no
 * partner, are left out of the level as well as the noise, so the two measure the same pixels.
 * Resampling cannot make the two pixels alike at that spacing. Smooth light (a galaxy or a
 * gradient) changes little over that short distance, so it cancels in the difference. Only the
 * pixel-to-pixel noise (and star edges) remain.
 *
 * @param pixels Pixel values; one plane, or three planes one after another for colour.
 * @param width Image width in pixels.
 * @param height Image height in pixels.
 * @param channels Number of planes: 1 or 3.
 * @returns The sampled pixel values and, in the same order, the differences to their partners.
 */
export function sampleSkyAndDifferences(
  pixels: Float32Array,
  width: number,
  height: number,
  channels: number,
): { sample: Float32Array; differences: Float32Array } {
  const planeCount = Math.max(1, channels);
  const pixelsPerPlane = width * height;
  const samplesPerPlane = Math.max(1, Math.floor(ZSCALE_SAMPLE_COUNT / planeCount));
  // The same step as sampleImageGrid, so the positions match.
  const step = Math.max(1, Math.floor(Math.sqrt(pixelsPerPlane / samplesPerPlane)));
  const sample: number[] = [];
  const differences: number[] = [];
  for (let plane = 0; plane < planeCount; plane++) {
    const planeStart = plane * pixelsPerPlane;
    for (let row = Math.floor(step / 2); row < height; row += step) {
      for (let column = Math.floor(step / 2); column < width - NEIGHBOR_DIFFERENCE_SPACING_PX; column += step) {
        const index = planeStart + row * width + column;
        const value = pixels[index];
        const difference = pixels[index + NEIGHBOR_DIFFERENCE_SPACING_PX] - value;
        if (Number.isFinite(value) && Number.isFinite(difference)) {
          sample.push(value);
          differences.push(difference);
        }
      }
    }
  }
  return { sample: Float32Array.from(sample), differences: Float32Array.from(differences) };
}

/**
 * Collects the differences between sampled pixels and the pixels a few columns to their right.
 *
 * @param pixels Pixel values; one plane, or three planes one after another for colour.
 * @param width Image width in pixels.
 * @param height Image height in pixels.
 * @param channels Number of planes: 1 or 3.
 * @returns The differences; positions in the last columns (with no partner) or with a non-finite value are skipped.
 */
export function sampleNeighborDifferences(
  pixels: Float32Array,
  width: number,
  height: number,
  channels: number,
): Float32Array {
  return sampleSkyAndDifferences(pixels, width, height, channels).differences;
}

/**
 * Measures the sky level and noise of a sample with sigma clipping.
 *
 * The sky is the clipped median of the sample. The noise comes from the neighbor differences when
 * they are given, because they ignore smooth light that would widen the sample's spread. Without
 * them, the noise is the clipped spread of the sample itself.
 *
 * @param sample Sampled pixel values, all finite.
 * @param differences Optional neighbor differences from `sampleNeighborDifferences`.
 * @returns The sky level and noise, or null when there are too few pixels or the noise is not above zero.
 */
export function measureSkyFromSample(
  sample: Float32Array,
  differences?: Float32Array,
): { sky: number; sigma: number } | null {
  const fromSample = clippedMedianAndSigma(sample);
  if (!fromSample) return null;
  if (!differences) return { sky: fromSample.middle, sigma: fromSample.sigma };
  const fromDifferences = clippedMedianAndSigma(differences);
  if (!fromDifferences) return null;
  return { sky: fromSample.middle, sigma: fromDifferences.sigma / DIFFERENCE_NOISE_FACTOR };
}

/**
 * Works out the black and white points of the sky-based range from a sample of pixel values.
 *
 * @param sample Sampled pixel values, all finite.
 * @param differences Optional neighbor differences for the noise; see `measureSkyFromSample`.
 * @returns Black at the sky minus 2 sigma and white at the sky plus 10 sigma; null when the sky
 *   cannot be measured (too few pixels, or no noise).
 */
export function skyRangeFromSample(
  sample: Float32Array,
  differences?: Float32Array,
): { low: number; high: number } | null {
  const measured = measureSkyFromSample(sample, differences);
  if (!measured) return null;
  return {
    low: measured.sky + SKY_RANGE_BLACK_SIGMAS * measured.sigma,
    high: measured.sky + SKY_RANGE_WHITE_SIGMAS * measured.sigma,
  };
}

/**
 * Works out the black and white points for the Linear display of an image.
 *
 * Uses the grid of zscale for the sky level, and neighbor differences at the same positions for
 * the noise (see `sampleSkyAndDifferences`). When the sky cannot be measured, it falls back to the
 * zscale range.
 *
 * @param pixels Pixel values; one plane, or three planes one after another for colour.
 * @param width Image width in pixels.
 * @param height Image height in pixels.
 * @param channels Number of planes: 1 or 3.
 * @returns The value to draw black and the value to draw white; null when the image has no
 *   finite pixel to sample.
 */
export function computeSkyRange(
  pixels: Float32Array,
  width: number,
  height: number,
  channels: number,
): { low: number; high: number } | null {
  const { sample, differences } = sampleSkyAndDifferences(pixels, width, height, channels);
  return skyRangeFromSample(sample, differences) ?? computeZscaleRange(pixels, width, height, channels);
}
