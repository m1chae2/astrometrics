/**
 * @file zscale.ts
 * @description The "zscale" brightness range used by IRAF and DS9, for the viewer's Linear display.
 *
 * A star field is mostly sky. The range from the darkest to the brightest pixel is set by a few
 * saturated stars, so a straight-line scale over it shows the sky as almost black. Zscale looks
 * only at the sky instead. It takes a small, evenly spaced sample of pixels, sorts them by value,
 * and fits a straight line to the middle of that sorted list, ignoring the faint and bright ends.
 * The slope of the line measures how much the sky varies from pixel to pixel. The black and white
 * points are set a fixed number of those steps below and above the sky level.
 *
 * Reference: the IRAF `display` task's zscale algorithm, as used by DS9 and astropy's
 * `ZScaleInterval`.
 */

/** About how many pixels to sample. IRAF samples about 1000; a few more makes the line steadier. */
export const ZSCALE_SAMPLE_COUNT = 2000;

/** IRAF's default contrast. A smaller number gives a wider range between black and white. */
export const ZSCALE_CONTRAST = 0.25;

/** A sample pixel further than this many standard deviations from the line is rejected. */
const REJECTION_SIGMAS = 2.5;

/** The line fit is repeated at most this many times, each time without the rejected pixels. */
const MAXIMUM_FIT_ITERATIONS = 5;

/** The fit fails when it has to reject more than this fraction of the sample. */
const MAXIMUM_REJECTED_FRACTION = 0.5;

/** The fit fails when fewer than this many sample pixels are left. */
const MINIMUM_SAMPLE_PIXELS = 5;

/** Each rejected pixel also rejects this fraction of the sample on each side of it. */
const REJECTION_GROW_FRACTION = 0.01;

/**
 * Takes an evenly spaced sample of the image's finite pixels, skipping NaN and infinite values.
 *
 * The sample is a regular grid over the picture, so it does not follow a pattern along the rows.
 * For a colour image the same grid is taken from each of the three planes and combined.
 *
 * @param pixels Pixel values; one plane, or three planes one after another for colour.
 * @param width Image width in pixels.
 * @param height Image height in pixels.
 * @param channels Number of planes: 1 or 3.
 * @param sampleCount About how many pixels to take.
 * @returns The sampled values, in no particular order.
 */
export function sampleImageGrid(
  pixels: Float32Array,
  width: number,
  height: number,
  channels: number,
  sampleCount: number = ZSCALE_SAMPLE_COUNT,
): Float32Array {
  const planeCount = Math.max(1, channels);
  const pixelsPerPlane = width * height;
  const samplesPerPlane = Math.max(1, Math.floor(sampleCount / planeCount));
  // One step in both directions, so the grid covers the picture evenly.
  const step = Math.max(1, Math.floor(Math.sqrt(pixelsPerPlane / samplesPerPlane)));
  const sampled: number[] = [];
  for (let plane = 0; plane < planeCount; plane++) {
    const planeStart = plane * pixelsPerPlane;
    for (let row = Math.floor(step / 2); row < height; row += step) {
      for (let column = Math.floor(step / 2); column < width; column += step) {
        const value = pixels[planeStart + row * width + column];
        if (Number.isFinite(value)) sampled.push(value);
      }
    }
  }
  return Float32Array.from(sampled);
}

/**
 * Fits a straight line to the sorted values by least squares, leaving out the rejected ones.
 *
 * @param sortedValues Sample values in rising order. The position in this list is the line's x.
 * @param isRejected True for each value to leave out.
 * @returns The line's slope and intercept, or null when fewer than two values are used.
 */
function fitLine(
  sortedValues: Float32Array,
  isRejected: Uint8Array,
): { slope: number; intercept: number } | null {
  let count = 0;
  let sumX = 0;
  let sumY = 0;
  let sumXX = 0;
  let sumXY = 0;
  for (let index = 0; index < sortedValues.length; index++) {
    if (isRejected[index]) continue;
    const y = sortedValues[index];
    count += 1;
    sumX += index;
    sumY += y;
    sumXX += index * index;
    sumXY += index * y;
  }
  if (count < 2) return null;
  const denominator = count * sumXX - sumX * sumX;
  if (denominator === 0) return null;
  const slope = (count * sumXY - sumX * sumY) / denominator;
  return { slope, intercept: (sumY - slope * sumX) / count };
}

/**
 * Works out the black and white points from a sample of pixel values.
 *
 * Steps: sort the sample; fit a line to the sorted values; reject values more than 2.5 standard
 * deviations from the line, with a few neighbors on each side of each; fit again, up to five
 * times. The slope of the final line, divided by the contrast, is the step in brightness between
 * neighboring ranks. Black and white are set that many steps below and above the median, for
 * the whole length of the list, and kept inside the sample's own minimum and maximum.
 * When the fit fails, black and white are the sample's minimum and maximum.
 *
 * @param sample Sampled pixel values, all finite.
 * @param contrast IRAF contrast; smaller values give a wider range. Default 0.25.
 * @returns The value to draw black and the value to draw white, with black below white; null
 *   when the sample is empty.
 */
export function zscaleFromSample(
  sample: Float32Array,
  contrast: number = ZSCALE_CONTRAST,
): { low: number; high: number } | null {
  const pixelCount = sample.length;
  if (pixelCount === 0) return null;
  const sortedValues = Float32Array.from(sample).sort();
  const sampleMinimum = sortedValues[0];
  const sampleMaximum = sortedValues[pixelCount - 1];
  if (sampleMinimum === sampleMaximum) return { low: sampleMinimum, high: sampleMaximum };

  const centerIndex = Math.floor((pixelCount - 1) / 2);
  const median = pixelCount % 2 === 1
    ? sortedValues[centerIndex]
    : (sortedValues[centerIndex] + sortedValues[centerIndex + 1]) / 2;

  const minimumGoodPixels = Math.max(MINIMUM_SAMPLE_PIXELS, Math.floor(pixelCount * (1 - MAXIMUM_REJECTED_FRACTION)));
  const growRadius = Math.max(1, Math.floor(pixelCount * REJECTION_GROW_FRACTION));
  const isRejected = new Uint8Array(pixelCount);
  let goodPixelCount = pixelCount;
  let lastGoodPixelCount = pixelCount + 1;
  let line: { slope: number; intercept: number } | null = null;

  for (let iteration = 0; iteration < MAXIMUM_FIT_ITERATIONS; iteration++) {
    if (goodPixelCount >= lastGoodPixelCount || goodPixelCount < minimumGoodPixels) break;
    line = fitLine(sortedValues, isRejected);
    if (!line) break;

    // The scatter of the kept pixels about the line sets how far a pixel may stray.
    let sumSquares = 0;
    let keptCount = 0;
    for (let index = 0; index < pixelCount; index++) {
      if (isRejected[index]) continue;
      const residual = sortedValues[index] - (line.intercept + line.slope * index);
      sumSquares += residual * residual;
      keptCount += 1;
    }
    const threshold = REJECTION_SIGMAS * Math.sqrt(sumSquares / Math.max(1, keptCount));

    // Reject the strays and the neighbors on each side of each.
    const strayIndexes: number[] = [];
    for (let index = 0; index < pixelCount; index++) {
      const residual = sortedValues[index] - (line.intercept + line.slope * index);
      if (Math.abs(residual) > threshold) strayIndexes.push(index);
    }
    isRejected.fill(0);
    for (const strayIndex of strayIndexes) {
      const first = Math.max(0, strayIndex - growRadius);
      const last = Math.min(pixelCount - 1, strayIndex + growRadius);
      for (let index = first; index <= last; index++) isRejected[index] = 1;
    }
    lastGoodPixelCount = goodPixelCount;
    goodPixelCount = pixelCount - isRejected.reduce((total, flag) => total + flag, 0);
  }

  if (!line || goodPixelCount < minimumGoodPixels) return { low: sampleMinimum, high: sampleMaximum };

  const slope = contrast > 0 ? line.slope / contrast : line.slope;
  const low = Math.max(sampleMinimum, median - (centerIndex - 1) * slope);
  const high = Math.min(sampleMaximum, median + (pixelCount - centerIndex) * slope);
  if (!(high > low)) return { low: sampleMinimum, high: sampleMaximum };
  return { low, high };
}

/**
 * Works out the black and white points for the Linear display of an image.
 *
 * @param pixels Pixel values; one plane, or three planes one after another for colour.
 * @param width Image width in pixels.
 * @param height Image height in pixels.
 * @param channels Number of planes: 1 or 3. A colour image gets one range from all three planes.
 * @returns The value to draw black and the value to draw white; null when the image has no
 *   finite pixel to sample.
 */
export function computeZscaleRange(
  pixels: Float32Array,
  width: number,
  height: number,
  channels: number,
): { low: number; high: number } | null {
  return zscaleFromSample(sampleImageGrid(pixels, width, height, channels));
}
