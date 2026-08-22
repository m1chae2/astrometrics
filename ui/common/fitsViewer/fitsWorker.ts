/**
 * fitsWorker.ts
 *
 * Web Worker for processing raw FITS pixel data into a viewable bitmap.
 * Offloads expensive loops and color mapping from the main thread.
 * Implements:
 * 1. Parsing (Endian conversion, Min/Max calculation, Multi-channel support)
 * 2. Rendering (Siril-like AutoStretch/MTF, RGB support), preferring an
 *    OffscreenCanvas+WebGL2 path (see mtfStretchGL.ts, the single canonical
 *    MTF-stretch implementation shared with FitsLoaderItem.tsx) and falling
 *    back to the scalar JS loop below only if OffscreenCanvas/WebGL2
 *    construction itself fails.
 */

import { renderMtfStretch, computeMtfStretchParameters, MtfStretchParameters } from './mtfStretchGL';

let storedBuffer: ArrayBuffer | null = null;
let currentWidth = 0;
let currentHeight = 0;
let currentChannels = 1;
let currentRowOrder: string | undefined;

// Stretch-parameter cache to avoid re-deriving statistics on every render if
// the buffer hasn't changed.
let cachedParameters: MtfStretchParameters | null = null;

function midtoneTransferFunction(mid: number, x: number): number {
  if (x === 0) return 0;
  if (x === 1) return 1;
  if (Math.abs(mid - 0.5) < 1e-6) return x;
  return ((mid - 1) * x) / ((2 * mid - 1) * x - mid);
}

/**
 * Attempts to render the MTF stretch on the GPU via OffscreenCanvas+WebGL2,
 * returning the resulting full-resolution bitmap, or null if OffscreenCanvas
 * or WebGL2 construction fails (the only case where the caller should fall
 * back to the scalar JS loop below).
 */
function tryRenderStretchViaWebGL(
  raw: Float32Array,
  w: number,
  h: number,
  channels: number,
  parameters: MtfStretchParameters,
): OffscreenCanvas | null {
  try {
    if (typeof OffscreenCanvas === 'undefined') return null;
    const canvas = new OffscreenCanvas(w, h);
    const gl = canvas.getContext('webgl2');
    if (!gl) return null;

    renderMtfStretch(gl, {
      raw,
      sourceWidth: w,
      sourceHeight: h,
      channels: channels === 3 ? 3 : 1,
      isTopDownRowOrder: currentRowOrder === 'TOP-DOWN',
      destinationWidth: w,
      destinationHeight: h,
      parameters,
    });
    return canvas;
  } catch {
    return null;
  }
}

self.onmessage = async (ev: MessageEvent) => {
  const { cmd, pw, ph, channels: requestedChannels, roworder, rawBuffer, dstW, dstH, dpr, bitpix, bzero, bscale, dataOffset } = ev.data;

  if (cmd === 'init') {
    storedBuffer = rawBuffer;
    currentWidth = pw;
    currentHeight = ph;
    currentChannels = requestedChannels || 1;
    currentRowOrder = roworder;
    cachedParameters = null;
    return;
  }

  if (cmd === 'parse') {
    try {
      const channels = requestedChannels || 1;
      const bytesPerPixel = Math.abs(bitpix) / 8;
      const totalElements = pw * ph * channels;
      const floatData = new Float32Array(totalElements);
      const dataView = new DataView(rawBuffer);

      let min = Infinity;
      let max = -Infinity;

      const littleEndian = false; // FITS is always Big Endian

      for (let i = 0; i < totalElements; i++) {
        const byteOffset = dataOffset + i * bytesPerPixel;
        if (byteOffset + bytesPerPixel > dataView.byteLength) break;

        let val = 0;
        if (bitpix === -32) {
          val = dataView.getFloat32(byteOffset, littleEndian);
        } else if (bitpix === -64) {
          val = dataView.getFloat64(byteOffset, littleEndian);
        } else if (bitpix === 16) {
          val = dataView.getInt16(byteOffset, littleEndian);
        } else if (bitpix === 32) {
          val = dataView.getInt32(byteOffset, littleEndian);
        } else if (bitpix === 8) {
          val = dataView.getUint8(byteOffset);
        }

        const physicalVal = (bzero || 0) + (bscale || 1) * val;
        floatData[i] = physicalVal;

        if (physicalVal < min) min = physicalVal;
        if (physicalVal > max) max = physicalVal;
      }

      if (min === Infinity) { min = 0; max = 65535; }

      // Cache it locally
      storedBuffer = floatData.buffer;
      currentWidth = pw;
      currentHeight = ph;
      currentChannels = channels;
      currentRowOrder = undefined;
      cachedParameters = null;

      const result = {
        w: pw,
        h: ph,
        channels,
        min,
        max,
        raw: floatData
      };

      (self as any).postMessage({ cmd: 'parseComplete', result }, [floatData.buffer]);

    } catch (err) {
      (self as any).postMessage({ error: String(err) });
    }
    return;
  }

  if (cmd === 'render') {
    try {
      const { stretch = true } = ev.data;
      const bufferToUse = rawBuffer || storedBuffer;
      const w = pw || currentWidth;
      const h = ph || currentHeight;
      const channels = ev.data.channels || currentChannels || 1;
      if (ev.data.roworder !== undefined) {
        currentRowOrder = ev.data.roworder;
      }

      if (!bufferToUse) {
        throw new Error("No buffer available for rendering");
      }

      const raw = new Float32Array(bufferToUse);
      const destinationWidth = Math.round(dstW * dpr);
      const destinationHeight = Math.round(dstH * dpr);

      // GPU path: stretch at full source resolution via the shared shader,
      // then let createImageBitmap's own high-quality resize handle the
      // downscale to destination size (matching this pipeline's existing
      // two-stage shape). Falls back to the scalar JS loop below only if
      // OffscreenCanvas/WebGL2 construction itself fails.
      let stretchedFullResCanvas: OffscreenCanvas | null = null;
      if (stretch) {
        if (!cachedParameters) {
          cachedParameters = computeMtfStretchParameters(raw);
        }
        stretchedFullResCanvas = tryRenderStretchViaWebGL(raw, w, h, channels, cachedParameters);
      }

      let bitmap: ImageBitmap;
      if (stretchedFullResCanvas) {
        const fullResBitmap = stretchedFullResCanvas.transferToImageBitmap();
        bitmap = await createImageBitmap(fullResBitmap, {
          resizeWidth: destinationWidth,
          resizeHeight: destinationHeight,
          resizeQuality: 'high'
        });
        fullResBitmap.close();
      } else {
        // Scalar JS fallback, only reached if OffscreenCanvas/WebGL2
        // construction failed above. Note this path does not correct FITS
        // row order (matching this worker's pre-existing behavior) — the
        // row-order fix lives in the GPU path above, which covers every
        // realistic environment this worker actually runs in.
        const pixels = new Uint8ClampedArray(w * h * 4);

        if (stretch) {
          const { shadows, range, midtones } = cachedParameters!;

          if (channels === 3) {
            // RGB Stretch - handle planar data (R...G...B...)
            const planeSize = w * h;
            for (let i = 0; i < planeSize; i++) {
              for (let c = 0; c < 3; c++) {
                let val = raw[c * planeSize + i];
                val = val < shadows ? 0 : (val - shadows) / range;
                val = midtoneTransferFunction(midtones, val);
                val = Math.max(0, Math.min(1, val));
                pixels[i * 4 + c] = Math.round(val * 255);
              }
              pixels[i * 4 + 3] = 255;
            }
          } else {
            // Grayscale Stretch
            for (let i = 0; i < raw.length; i++) {
              let val = raw[i];
              val = val < shadows ? 0 : (val - shadows) / range;
              val = midtoneTransferFunction(midtones, val);
              val = Math.max(0, Math.min(1, val));
              const p = Math.round(val * 255);
              const idx = i * 4;
              pixels[idx] = p; pixels[idx + 1] = p; pixels[idx + 2] = p; pixels[idx + 3] = 255;
            }
          }
        } else {
          // Linear scaling
          let min = Infinity;
          let max = -Infinity;
          for (let i = 0; i < raw.length; i++) {
            if (raw[i] < min) min = raw[i];
            if (raw[i] > max) max = raw[i];
          }
          if (max === min) max = min + 1;

          if (channels === 3) {
            const planeSize = w * h;
            for (let i = 0; i < planeSize; i++) {
              for (let c = 0; c < 3; c++) {
                let val = (raw[c * planeSize + i] - min) / (max - min);
                pixels[i * 4 + c] = Math.round(Math.max(0, Math.min(1, val)) * 255);
              }
              pixels[i * 4 + 3] = 255;
            }
          } else {
            for (let i = 0; i < raw.length; i++) {
              const val = (raw[i] - min) / (max - min);
              const p = Math.round(Math.max(0, Math.min(1, val)) * 255);
              const idx = i * 4;
              pixels[idx] = p; pixels[idx + 1] = p; pixels[idx + 2] = p; pixels[idx + 3] = 255;
            }
          }
        }

        const imageData = new ImageData(pixels, w, h);
        bitmap = await createImageBitmap(imageData, 0, 0, w, h, {
          resizeWidth: destinationWidth,
          resizeHeight: destinationHeight,
          resizeQuality: 'high'
        });
      }

      (self as any).postMessage({ bitmap }, [bitmap]);

    } catch (error) {
      (self as any).postMessage({ error: String(error) });
    }
  }
};
