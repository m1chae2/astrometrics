/**
 * @file test_zscale.test.ts
 * @description Checks the zscale black and white points on synthetic star fields.
 */
import { describe, expect, it } from 'vitest';
import { computeZscaleRange, sampleImageGrid, zscaleFromSample } from '../zscale';

/**
 * Makes a deterministic source of numbers between 0 and 1 (a small linear congruential generator).
 *
 * @param seed Starting value.
 * @returns A function that gives the next number each time it is called.
 */
function makeRandom(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

/**
 * Makes one plane of flat sky with Gaussian noise.
 *
 * @param width Width in pixels.
 * @param height Height in pixels.
 * @param sky Sky level.
 * @param sigma Noise standard deviation.
 * @param random Source of numbers between 0 and 1.
 * @returns The plane.
 */
function makeSkyPlane(width: number, height: number, sky: number, sigma: number, random: () => number): Float32Array {
  const plane = new Float32Array(width * height);
  for (let index = 0; index < plane.length; index++) {
    // Box-Muller: two uniform numbers make one Gaussian number.
    const radius = Math.sqrt(-2 * Math.log(1 - random()));
    plane[index] = sky + sigma * radius * Math.cos(2 * Math.PI * random());
  }
  return plane;
}

describe('computeZscaleRange', () => {
  const width = 400;
  const height = 300;
  const sky = 1000;
  const sigma = 10;

  it('sets black and white a few sigma around the sky, not up to the saturated stars', () => {
    const plane = makeSkyPlane(width, height, sky, sigma, makeRandom(7));
    // A handful of saturated stars, each a small bright square.
    for (const [starX, starY] of [[50, 60], [200, 150], [330, 240], [120, 250], [300, 40]]) {
      for (let dy = -3; dy <= 3; dy++) {
        for (let dx = -3; dx <= 3; dx++) plane[(starY + dy) * width + starX + dx] = 65535;
      }
    }
    const range = computeZscaleRange(plane, width, height, 1)!;
    expect(range.low).toBeLessThan(sky);
    expect(range.high).toBeGreaterThan(sky);
    // A few sigma on each side, and nowhere near the 65535 of the stars.
    expect(range.low).toBeGreaterThan(sky - 12 * sigma);
    expect(range.high).toBeLessThan(sky + 12 * sigma);
    expect(range.high - range.low).toBeGreaterThan(2 * sigma);
  });

  it('gives one range for a colour image from the combined planes', () => {
    const random = makeRandom(11);
    const red = makeSkyPlane(width, height, 500, 5, random);
    const green = makeSkyPlane(width, height, 500, 5, random);
    const blue = makeSkyPlane(width, height, 500, 5, random);
    const combined = new Float32Array(width * height * 3);
    combined.set(red, 0);
    combined.set(green, width * height);
    combined.set(blue, 2 * width * height);
    const range = computeZscaleRange(combined, width, height, 3)!;
    expect(range.low).toBeGreaterThan(500 - 12 * 5);
    expect(range.high).toBeLessThan(500 + 12 * 5);
    expect(range.high).toBeGreaterThan(range.low);
  });

  it('ignores NaN pixels', () => {
    const plane = makeSkyPlane(width, height, sky, sigma, makeRandom(3));
    for (let index = 0; index < plane.length; index += 7) plane[index] = NaN;
    const range = computeZscaleRange(plane, width, height, 1)!;
    expect(Number.isFinite(range.low)).toBe(true);
    expect(Number.isFinite(range.high)).toBe(true);
    expect(range.high).toBeGreaterThan(range.low);
  });

  it('takes about 2000 samples from a large image', () => {
    const plane = new Float32Array(2000 * 1500).fill(1);
    const sample = sampleImageGrid(plane, 2000, 1500, 1);
    expect(sample.length).toBeGreaterThan(1000);
    expect(sample.length).toBeLessThan(4000);
  });

  it('returns null for an image with no finite pixel', () => {
    expect(computeZscaleRange(new Float32Array(100).fill(NaN), 10, 10, 1)).toBeNull();
  });
});

describe('zscaleFromSample', () => {
  it('falls back to the sample minimum and maximum when the fit fails', () => {
    // Four values: fewer than the five pixels the fit needs.
    expect(zscaleFromSample(Float32Array.from([4, 1, 3, 2]))).toEqual({ low: 1, high: 4 });
  });

  it('gives the single value for a flat sample', () => {
    expect(zscaleFromSample(new Float32Array(50).fill(7))).toEqual({ low: 7, high: 7 });
  });
});
