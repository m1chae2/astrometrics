/**
 * @fileoverview Unit tests for the FITS pixel decoder.
 * The fast typed-array paths must give exactly the same pixels and brightness
 * range as the slower, always-correct DataView reference, for every supported
 * pixel type, with header scaling, and for files cut short.
 */

import { describe, it, expect } from 'vitest';
import {
  decodeFitsPixels,
  decodeFitsPixelsWithDataView,
} from '../common/fitsViewer/fitsParse';

/** Writes `values` big-endian into a buffer after a fake header of `dataOffset` bytes. */
function buildFitsBuffer(
  values: number[],
  bitpix: number,
  dataOffset: number,
  trailingBytesToCut = 0,
): ArrayBuffer {
  const bytesPerPixel = Math.abs(bitpix) / 8;
  const buffer = new ArrayBuffer(dataOffset + values.length * bytesPerPixel);
  const view = new DataView(buffer);
  values.forEach((value, index) => {
    const offset = dataOffset + index * bytesPerPixel;
    if (bitpix === 8) view.setUint8(offset, value);
    else if (bitpix === 16) view.setInt16(offset, value, false);
    else if (bitpix === 32) view.setInt32(offset, value, false);
    else if (bitpix === -32) view.setFloat32(offset, value, false);
    else if (bitpix === -64) view.setFloat64(offset, value, false);
  });
  return trailingBytesToCut > 0 ? buffer.slice(0, buffer.byteLength - trailingBytesToCut) : buffer;
}

/** Deterministic pseudo-random values that fit the given pixel type. */
function sampleValues(bitpix: number, count: number): number[] {
  let seed = 7;
  const next = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 0xffffffff;
  };
  return Array.from({ length: count }, () => {
    if (bitpix === 8) return Math.floor(next() * 256);
    if (bitpix === 16) return Math.floor(next() * 65536) - 32768;
    if (bitpix === 32) return Math.floor(next() * 2 ** 32) - 2 ** 31;
    return (next() - 0.5) * 2000;
  });
}

describe('decodeFitsPixels', () => {
  for (const bitpix of [8, 16, 32, -32, -64]) {
    for (const [bzero, bscale] of [[0, 1], [32768, 1], [100, 0.5]]) {
      it(`matches the DataView reference for BITPIX ${bitpix}, BZERO ${bzero}, BSCALE ${bscale}`, () => {
        const values = sampleValues(bitpix, 5 * 7 * 2);
        const buffer = buildFitsBuffer(values, bitpix, 2880);

        const fast = decodeFitsPixels(buffer, 2880, 5, 7, 2, bitpix, bzero, bscale);
        const reference = decodeFitsPixelsWithDataView(buffer, 2880, 5 * 7 * 2, bitpix, bzero, bscale);

        expect(Array.from(fast.pixels)).toEqual(Array.from(reference.pixels));
        expect(fast.min).toBe(reference.min);
        expect(fast.max).toBe(reference.max);
      });
    }
  }

  it('leaves the pixels missing from a truncated file as 0, like the reference', () => {
    const values = sampleValues(16, 20);
    const buffer = buildFitsBuffer(values, 16, 2880, 10);

    const fast = decodeFitsPixels(buffer, 2880, 5, 4, 1, 16, 0, 1);
    const reference = decodeFitsPixelsWithDataView(buffer, 2880, 20, 16, 0, 1);

    expect(Array.from(fast.pixels)).toEqual(Array.from(reference.pixels));
    expect(fast.pixels.slice(15)).toEqual(new Float32Array(5));
    expect(fast.min).toBe(reference.min);
    expect(fast.max).toBe(reference.max);
  });

  it('ignores NaN when finding the brightness range', () => {
    const buffer = buildFitsBuffer([5, NaN, -3, 9], -32, 2880);

    const decoded = decodeFitsPixels(buffer, 2880, 2, 2, 1, -32, 0, 1);

    expect(decoded.min).toBe(-3);
    expect(decoded.max).toBe(9);
  });

  it('falls back to a 0 to 65535 range when there is no pixel data', () => {
    const decoded = decodeFitsPixels(new ArrayBuffer(2880), 2880, 2, 2, 1, 16, 0, 1);

    expect(decoded.min).toBe(0);
    expect(decoded.max).toBe(65535);
  });

  it('decodes a 16-bit pixel with BZERO 32768 as an unsigned value', () => {
    const buffer = buildFitsBuffer([-32768, 0, 32767], 16, 2880);

    const decoded = decodeFitsPixels(buffer, 2880, 3, 1, 1, 16, 32768, 1);

    expect(Array.from(decoded.pixels)).toEqual([0, 32768, 65535]);
  });
});
