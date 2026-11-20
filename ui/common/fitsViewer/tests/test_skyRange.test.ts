/**
 * @file test_skyRange.test.ts
 * @description Checks that the Linear range follows the sky's noise and not what fills the frame.
 */
import { describe, expect, it } from 'vitest';
import {
  computeSkyRange,
  measureSkyFromSample,
  sampleNeighborDifferences,
  skyRangeFromSample,
} from '../skyRange';
import { sampleImageGrid } from '../zscale';

/**
 * Makes a deterministic source of numbers between 0 and 1.
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
 * Makes sky values with Gaussian noise.
 *
 * @param count Number of values.
 * @param sky Sky level.
 * @param sigma Noise standard deviation.
 * @param seed Seed for the random numbers.
 * @returns The values.
 */
function makeNoise(count: number, sky: number, sigma: number, seed: number): Float32Array {
  const random = makeRandom(seed);
  const values = new Float32Array(count);
  for (let index = 0; index < count; index++) {
    const radius = Math.sqrt(-2 * Math.log(random() || 1e-12));
    values[index] = sky + sigma * radius * Math.cos(2 * Math.PI * random());
  }
  return values;
}

describe('skyRangeFromSample', () => {
  it('sets black at sky minus 2 sigma and white at sky plus 10 sigma for pure noise', () => {
    const range = skyRangeFromSample(makeNoise(4000, 0.2, 0.01, 1));
    expect(range).not.toBeNull();
    expect(range!.low).toBeCloseTo(0.2 - 2 * 0.01, 2);
    expect(range!.high).toBeCloseTo(0.2 + 10 * 0.01, 2);
  });

  it('gives the same range when a large bright region fills part of the frame', () => {
    const sky = makeNoise(3000, 0.2, 0.01, 2);
    const withGalaxy = new Float32Array(4000);
    withGalaxy.set(sky);
    withGalaxy.set(makeNoise(1000, 0.6, 0.1, 3), 3000);
    const plain = skyRangeFromSample(sky)!;
    const crowded = skyRangeFromSample(withGalaxy)!;
    expect(crowded.low).toBeCloseTo(plain.low, 2);
    expect(crowded.high).toBeCloseTo(plain.high, 2);
  });
});

describe('noise from neighbor differences', () => {
  it('ignores a large smooth galaxy and a gradient when measuring the noise', () => {
    const width = 400;
    const height = 400;
    const trueSigma = 0.01;
    const noise = makeNoise(width * height, 0, trueSigma, 7);
    const image = new Float32Array(width * height);
    for (let row = 0; row < height; row++) {
      for (let column = 0; column < width; column++) {
        const dx = column - 200;
        const dy = row - 200;
        const galaxy = 0.5 * Math.exp(-(dx * dx + dy * dy) / (2 * 90 * 90));
        const gradient = 0.1 * (column / width);
        image[row * width + column] = 0.2 + galaxy + gradient + noise[row * width + column];
      }
    }
    const sample = sampleImageGrid(image, width, height, 1);
    const differences = sampleNeighborDifferences(image, width, height, 1);
    const withDifferences = measureSkyFromSample(sample, differences)!;
    const sampleOnly = measureSkyFromSample(sample)!;
    expect(Math.abs(withDifferences.sigma - trueSigma) / trueSigma).toBeLessThan(0.1);
    expect(sampleOnly.sigma).toBeGreaterThan(2 * trueSigma);
    const range = computeSkyRange(image, width, height, 1)!;
    expect((range.high - range.low) / 12).toBeCloseTo(trueSigma, 2);
  });
});

describe('noise that is correlated between neighbors', () => {
  it('recovers the true per-pixel noise of smoothed noise', () => {
    const width = 400;
    const height = 400;
    const rawSigma = 0.02;
    const raw = makeNoise(width * height, 0, rawSigma, 11);
    // Averaging each pixel with its right neighbor mimics resampling: neighbors share noise.
    const image = new Float32Array(width * height);
    for (let index = 0; index < image.length; index++) {
      const right = index + 1 < image.length ? raw[index + 1] : raw[index];
      image[index] = 0.2 + (raw[index] + right) / 2;
    }
    // Averaging two independent pixels lowers the noise by the square root of 2.
    const trueSigma = rawSigma / Math.SQRT2;
    const sample = sampleImageGrid(image, width, height, 1);
    const differences = sampleNeighborDifferences(image, width, height, 1);
    const measured = measureSkyFromSample(sample, differences)!;
    expect(Math.abs(measured.sigma - trueSigma) / trueSigma).toBeLessThan(0.1);
  });
});

describe('computeSkyRange fallbacks', () => {
  it('falls back to zscale when the noise is zero', () => {
    const flat = new Float32Array(100 * 100).fill(0.3);
    expect(skyRangeFromSample(flat)).toBeNull();
    const range = computeSkyRange(flat, 100, 100, 1)!;
    expect(range.low).toBeCloseTo(0.3, 5);
    expect(range.high).toBeCloseTo(0.3, 5);
  });

  it('returns null for an image of only NaN', () => {
    const empty = new Float32Array(100 * 100).fill(NaN);
    expect(computeSkyRange(empty, 100, 100, 1)).toBeNull();
  });

  it('falls back to zscale for a tiny image', () => {
    const tiny = Float32Array.from([1, 2, 3, 4]);
    expect(skyRangeFromSample(tiny)).toBeNull();
    expect(computeSkyRange(tiny, 2, 2, 1)).not.toBeNull();
  });
});
