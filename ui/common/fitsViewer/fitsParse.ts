/**
 * @module fitsParse
 * @fileoverview Turns the raw pixel bytes of a FITS image into real brightness values.
 *
 * A FITS file stores its pixels as big-endian numbers (the most significant
 * byte comes first), in one of a few number types chosen by the `BITPIX`
 * header value. Most computers are little-endian, so every number has to have
 * its bytes reversed before it can be read. This module does that, applies the
 * `BZERO`/`BSCALE` scaling from the header, and records the smallest and
 * largest value it saw.
 *
 * The common cases (8-bit, 16-bit, 32-bit integer and 32-bit float) read the
 * bytes straight into typed arrays and swap them with bit operations. That is
 * several times faster than reading each pixel through a `DataView`, which
 * matters for a 24-megapixel frame. Anything unusual (64-bit floats, a
 * big-endian computer, a data offset that is not aligned) falls back to the
 * slower `DataView` loop, which gives identical results.
 */

/** The pixels of a decoded FITS image, with their brightness range. */
export interface DecodedFitsPixels {
  /** Real (scaled) pixel values, one 32-bit float per pixel; planes of a colour image follow each other. */
  pixels: Float32Array;
  /** Smallest value found, ignoring NaN; 0 when there were no readable pixels. */
  min: number;
  /** Largest value found, ignoring NaN; 65535 when there were no readable pixels. */
  max: number;
}

/** Whether this computer stores multi-byte numbers with the least significant byte first. */
const HOST_IS_LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

/**
 * Fills in the brightness range, replacing an empty range with a sensible default.
 *
 * @param pixels - The decoded pixel values.
 * @param min - Smallest value seen, or `Infinity` when nothing was read.
 * @param max - Largest value seen, or `-Infinity` when nothing was read.
 * @returns The pixels with their brightness range.
 */
function withRange(pixels: Float32Array, min: number, max: number): DecodedFitsPixels {
  if (min === Infinity) return { pixels, min: 0, max: 65535 };
  return { pixels, min, max };
}

/**
 * Decodes FITS pixels one at a time through a `DataView`.
 *
 * This is the reference implementation: it works on any computer and for any
 * `BITPIX` value, and the fast paths must give the same answer. Pixels beyond
 * the end of a truncated file are left as 0.
 *
 * @param rawBuffer - The whole FITS file.
 * @param dataOffset - Byte position where the pixel data starts.
 * @param pixelCount - Number of values to decode (width x height x channels).
 * @param bitpix - FITS `BITPIX`: 8, 16, 32, -32 or -64.
 * @param bzero - FITS `BZERO` offset.
 * @param bscale - FITS `BSCALE` factor.
 * @returns The scaled pixels and their brightness range.
 */
export function decodeFitsPixelsWithDataView(
  rawBuffer: ArrayBuffer,
  dataOffset: number,
  pixelCount: number,
  bitpix: number,
  bzero: number,
  bscale: number,
): DecodedFitsPixels {
  const bytesPerPixel = Math.abs(bitpix) / 8;
  const pixels = new Float32Array(pixelCount);
  const dataView = new DataView(rawBuffer);
  const zero = bzero || 0;
  const scale = bscale || 1;
  let min = Infinity;
  let max = -Infinity;

  for (let index = 0; index < pixelCount; index++) {
    const byteOffset = dataOffset + index * bytesPerPixel;
    if (byteOffset + bytesPerPixel > dataView.byteLength) break;

    let value = 0;
    if (bitpix === -32) {
      value = dataView.getFloat32(byteOffset, false);
    } else if (bitpix === -64) {
      value = dataView.getFloat64(byteOffset, false);
    } else if (bitpix === 16) {
      value = dataView.getInt16(byteOffset, false);
    } else if (bitpix === 32) {
      value = dataView.getInt32(byteOffset, false);
    } else if (bitpix === 8) {
      value = dataView.getUint8(byteOffset);
    }

    const physicalValue = zero + scale * value;
    pixels[index] = physicalValue;
    if (physicalValue < min) min = physicalValue;
    if (physicalValue > max) max = physicalValue;
  }

  return withRange(pixels, min, max);
}

/**
 * Decodes the pixel data of a FITS image into 32-bit floats.
 *
 * @param rawBuffer - The whole FITS file.
 * @param dataOffset - Byte position where the pixel data starts (the end of the header).
 * @param width - Image width in pixels (`NAXIS1`).
 * @param height - Image height in pixels (`NAXIS2`).
 * @param channels - Number of colour planes (`NAXIS3`, or 1).
 * @param bitpix - FITS `BITPIX`: 8, 16, 32, -32 or -64.
 * @param bzero - FITS `BZERO` offset (0 when the header has none).
 * @param bscale - FITS `BSCALE` factor (1 when the header has none).
 * @returns The scaled pixels and their brightness range.
 */
export function decodeFitsPixels(
  rawBuffer: ArrayBuffer,
  dataOffset: number,
  width: number,
  height: number,
  channels: number,
  bitpix: number,
  bzero: number,
  bscale: number,
): DecodedFitsPixels {
  const pixelCount = width * height * channels;
  const bytesPerPixel = Math.abs(bitpix) / 8;
  const isFastType = bitpix === 8 || bitpix === 16 || bitpix === 32 || bitpix === -32;
  const isAligned = dataOffset % bytesPerPixel === 0;
  if (!isFastType || !HOST_IS_LITTLE_ENDIAN || !isAligned) {
    return decodeFitsPixelsWithDataView(rawBuffer, dataOffset, pixelCount, bitpix, bzero, bscale);
  }

  // A truncated file only supplies the pixels it actually holds; the rest stay 0.
  const availableCount = Math.max(0, Math.floor((rawBuffer.byteLength - dataOffset) / bytesPerPixel));
  const readCount = Math.min(pixelCount, availableCount);
  const pixels = new Float32Array(pixelCount);
  const zero = bzero || 0;
  const scale = bscale || 1;
  let min = Infinity;
  let max = -Infinity;

  if (bitpix === 8) {
    const source = new Uint8Array(rawBuffer, dataOffset, readCount);
    for (let index = 0; index < readCount; index++) {
      const physicalValue = zero + scale * source[index];
      pixels[index] = physicalValue;
      if (physicalValue < min) min = physicalValue;
      if (physicalValue > max) max = physicalValue;
    }
  } else if (bitpix === 16) {
    const source = new Uint16Array(rawBuffer, dataOffset, readCount);
    for (let index = 0; index < readCount; index++) {
      const stored = source[index];
      // Swap the two bytes, then reinterpret the 16 bits as a signed number.
      const signedValue = (((stored << 8) | (stored >> 8)) << 16) >> 16;
      const physicalValue = zero + scale * signedValue;
      pixels[index] = physicalValue;
      if (physicalValue < min) min = physicalValue;
      if (physicalValue > max) max = physicalValue;
    }
  } else if (bitpix === 32) {
    const source = new Uint32Array(rawBuffer, dataOffset, readCount);
    for (let index = 0; index < readCount; index++) {
      const stored = source[index];
      const signedValue =
        ((stored & 0xff) << 24) | ((stored & 0xff00) << 8) | ((stored >>> 8) & 0xff00) | (stored >>> 24);
      const physicalValue = zero + scale * signedValue;
      pixels[index] = physicalValue;
      if (physicalValue < min) min = physicalValue;
      if (physicalValue > max) max = physicalValue;
    }
  } else {
    // 32-bit float: swap the bytes of each number in place, inside the output array.
    const source = new Uint32Array(rawBuffer, dataOffset, readCount);
    const outputBits = new Uint32Array(pixels.buffer);
    for (let index = 0; index < readCount; index++) {
      const stored = source[index];
      outputBits[index] =
        (((stored & 0xff) << 24) | ((stored & 0xff00) << 8) | ((stored >>> 8) & 0xff00) | (stored >>> 24)) >>> 0;
    }
    const isUnscaled = zero === 0 && scale === 1;
    for (let index = 0; index < readCount; index++) {
      const physicalValue = isUnscaled ? pixels[index] : zero + scale * pixels[index];
      pixels[index] = physicalValue;
      if (physicalValue < min) min = physicalValue;
      if (physicalValue > max) max = physicalValue;
    }
  }

  return withRange(pixels, min, max);
}
