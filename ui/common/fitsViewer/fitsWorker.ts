/**
 * fitsWorker.ts
 *
 * Web Worker that decodes FITS pixel data, keeps it, and draws it as a bitmap.
 * Offloads expensive loops and colour mapping from the main thread.
 *
 * One worker does both jobs on purpose. The decoded pixels (tens to hundreds
 * of megabytes for a large frame) stay inside this worker, filed under an id,
 * and the main thread only ever sees the image's size and brightness range.
 * Nothing is copied between threads, and a later redraw (for example turning
 * the stretch on or off) reuses the pixels already here.
 *
 * Messages in (all carry a `requestId` that is echoed back):
 *   - `parse`   decode a raw FITS file and file the pixels under `imageId`.
 *   - `render`  draw the image filed under `imageId` into a bitmap, with the
 *               library's `stretchParameters` when the stretch is on (without
 *               them the view is linear; the stretch is never worked out here).
 *   - `release` forget the image filed under `imageId`.
 *   - `pixel`   read the original value (every channel) at one pixel of the image filed under
 *               `imageId`, for the viewer's pixel readout. The pixel is named by its place in the
 *               picture as drawn (column and row from the top), and the reply gives the file's own
 *               pixel position. Replies with `pixelComplete`.
 *
 * A `render` may carry `turn` (0, 90, 180 or 270): the picture is then drawn turned clockwise by
 * that many degrees, and the pixel readout follows. This only re-indexes the grid when drawing; the
 * decoded pixels are never changed.
 *
 * With the stretch off and no `displayRange`, the view is linear between the
 * black and white points set from the sky's noise (see skyRange.ts), worked out
 * once per image from the decoded pixels.
 *
 * Drawing prefers one long-lived OffscreenCanvas+WebGL2 context (see
 * mtfStretchGL.ts, the single canonical MTF-stretch implementation). The
 * context, its compiled shader and the pixel texture on the GPU are kept
 * between renders, so redrawing the same image only changes shader settings.
 * The scalar JS loops below are a fallback used only if WebGL2 cannot be
 * created.
 */

import {
  renderMtfStretch,
  shaderParametersFromLibraryStretch,
  computeLinearStretchParameters,
  MtfStretchParameters,
} from './mtfStretchGL';
import { decodeFitsPixels } from './fitsParse';
import { computeSkyRange } from './skyRange';
import { turnRgbaPixels, turnedCellToFileCell, turnedSize } from './displayTurn';

/** One decoded image held by the worker. */
interface StoredImage {
  pixels: Float32Array;
  width: number;
  height: number;
  channels: number;
  rowOrder: string | undefined;
  min: number;
  max: number;
  /** Black and white points of the Linear display, worked out the first time they are needed. */
  skyRange?: { low: number; high: number };
  /** True when the last draw put the file's first row at the top; the pixel readout needs it to find the row under the pointer. */
  lastDrawnFirstRowAtTop?: boolean;
  /** How far the last draw turned the picture, clockwise in degrees; the pixel readout needs it to find the file pixel under the pointer. */
  lastDrawnTurn?: number;
}

const storedImages = new Map<number, StoredImage>();

let glCanvas: OffscreenCanvas | null = null;
let glContext: WebGL2RenderingContext | null = null;

/**
 * Gives the black and white points of the Linear display, working them out on first use.
 *
 * @param image The image.
 * @returns The value drawn black and the value drawn white. Falls back to the image's
 *   darkest and brightest pixels when the image has no finite pixel to sample.
 */
function getSkyRange(image: StoredImage): { low: number; high: number } {
  if (!image.skyRange) {
    image.skyRange = computeSkyRange(image.pixels, image.width, image.height, image.channels)
      ?? { low: image.min, high: image.max };
  }
  return image.skyRange;
}

function midtoneTransferFunction(mid: number, x: number): number {
  if (x === 0) return 0;
  if (x === 1) return 1;
  if (Math.abs(mid - 0.5) < 1e-6) return x;
  return ((mid - 1) * x) / ((2 * mid - 1) * x - mid);
}

/**
 * Returns the worker's long-lived WebGL2 context, creating it on first use.
 *
 * @returns The context, or null if OffscreenCanvas or WebGL2 is unavailable.
 */
function getGlContext(): WebGL2RenderingContext | null {
  if (glContext) return glContext;
  try {
    if (typeof OffscreenCanvas === 'undefined') return null;
    const canvas = new OffscreenCanvas(1, 1);
    const context = canvas.getContext('webgl2');
    if (!context) return null;
    // A lost context takes its shader and texture with it; start over on the next render.
    canvas.addEventListener('webglcontextlost', () => discardGlContext());
    glCanvas = canvas;
    glContext = context;
    return glContext;
  } catch {
    return null;
  }
}

/** Forgets the WebGL context so the next render builds a fresh one. */
function discardGlContext(): void {
  glCanvas = null;
  glContext = null;
}

/**
 * Draws the image at full resolution on the GPU and hands back the result as a bitmap.
 *
 * @param imageId - Id the image is filed under; lets the GPU keep the pixels between renders.
 * @param image - The image to draw.
 * @param parameters - Black point, range and midtones balance to draw with.
 * @param isTopDownRowOrder - True to draw the file's first row at the top, false to draw it at the bottom.
 * @param turn - How far to turn the picture, clockwise in degrees (0, 90, 180 or 270).
 * @returns A full-resolution bitmap, or null if the GPU path is unavailable (use the JS fallback).
 */
function renderViaWebGL(
  imageId: number,
  image: StoredImage,
  parameters: MtfStretchParameters,
  isTopDownRowOrder: boolean,
  turn: number,
): ImageBitmap | null {
  const context = getGlContext();
  if (!context || !glCanvas) return null;
  try {
    const drawnSize = turnedSize(image.width, image.height, turn);
    renderMtfStretch(context, {
      raw: image.pixels,
      sourceWidth: image.width,
      sourceHeight: image.height,
      channels: image.channels === 3 ? 3 : 1,
      isTopDownRowOrder,
      turnDegrees: turn,
      destinationWidth: drawnSize.w,
      destinationHeight: drawnSize.h,
      parameters,
      textureKey: imageId,
    });
    return glCanvas.transferToImageBitmap();
  } catch {
    discardGlContext();
    return null;
  }
}

/**
 * Scalar JS fallback, only used when the WebGL path above is unavailable.
 * Note this path does not correct FITS row order (matching this worker's
 * pre-existing behavior); the row-order fix lives in the GPU path.
 *
 * @param image - The image to draw.
 * @param stretch - True for the auto-stretch, false for a plain linear view.
 * @param parameters - Stretch settings. With `stretch` false they hold the
 *   linear range (`shadows` is the value drawn black, `shadows + range` the
 *   value drawn white).
 * @param destinationWidth - Width of the bitmap to return.
 * @param destinationHeight - Height of the bitmap to return.
 * @param turn - How far to turn the picture, clockwise in degrees (0, 90, 180 or 270).
 * @returns The bitmap, resized to the destination size.
 */
async function renderViaScalarLoops(
  image: StoredImage,
  stretch: boolean,
  parameters: MtfStretchParameters,
  destinationWidth: number,
  destinationHeight: number,
  turn: number,
): Promise<ImageBitmap> {
  const { pixels: raw, width: w, height: h, channels } = image;
  const pixels = new Uint8ClampedArray(w * h * 4);

  if (stretch) {
    const { shadows, range, midtones } = parameters;

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
    // Linear scaling over the range in `parameters`
    const min = parameters.shadows;
    const max = parameters.shadows + parameters.range;

    if (channels === 3) {
      const planeSize = w * h;
      for (let i = 0; i < planeSize; i++) {
        for (let c = 0; c < 3; c++) {
          const val = (raw[c * planeSize + i] - min) / (max - min);
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

  const turned = turnRgbaPixels(pixels, w, h, turn);
  const imageData = new ImageData(turned.pixels, turned.width, turned.height);
  return createImageBitmap(imageData, 0, 0, turned.width, turned.height, {
    resizeWidth: destinationWidth,
    resizeHeight: destinationHeight,
    resizeQuality: 'high',
  });
}

self.onmessage = async (ev: MessageEvent) => {
  const { cmd, requestId, imageId } = ev.data;

  if (cmd === 'release') {
    storedImages.delete(imageId);
    return;
  }

  if (cmd === 'pixel') {
    const { column, rowFromTop } = ev.data;
    const image = storedImages.get(imageId);
    if (!image || !Number.isInteger(column) || !Number.isInteger(rowFromTop)) {
      (self as any).postMessage({ cmd: 'pixelComplete', requestId, imageId, result: null });
      return;
    }
    // The drawn picture may be turned, and has the file's first row at the top or at the bottom
    // (the FITS default); this finds the file's own pixel behind the cell.
    const fileCell = turnedCellToFileCell(
      column, rowFromTop, image.width, image.height,
      image.lastDrawnTurn ?? 0, image.lastDrawnFirstRowAtTop !== false,
    );
    if (!fileCell) {
      (self as any).postMessage({ cmd: 'pixelComplete', requestId, imageId, result: null });
      return;
    }
    const planeSize = image.width * image.height;
    const values: number[] = [];
    for (let channel = 0; channel < image.channels; channel++) {
      values.push(image.pixels[channel * planeSize + fileCell.fileRow * image.width + fileCell.column]);
    }
    // FITS pixel numbers count from 1.
    (self as any).postMessage({
      cmd: 'pixelComplete',
      requestId,
      imageId,
      result: { x: fileCell.column + 1, y: fileCell.fileRow + 1, values },
    });
    return;
  }

  if (cmd === 'parse') {
    try {
      const { pw, ph, channels: requestedChannels, roworder, rawBuffer, bitpix, bzero, bscale, dataOffset } = ev.data;
      const channels = requestedChannels || 1;
      const decoded = decodeFitsPixels(rawBuffer, dataOffset, pw, ph, channels, bitpix, bzero, bscale);

      storedImages.set(imageId, {
        pixels: decoded.pixels,
        width: pw,
        height: ph,
        channels,
        rowOrder: roworder,
        min: decoded.min,
        max: decoded.max,
      });

      (self as any).postMessage({
        cmd: 'parseComplete',
        requestId,
        imageId,
        result: { w: pw, h: ph, channels, min: decoded.min, max: decoded.max },
      });
    } catch (err) {
      (self as any).postMessage({ cmd: 'error', requestId, error: String(err) });
    }
    return;
  }

  if (cmd === 'render') {
    try {
      const { dstW, dstH, dpr, stretch = true, displayRange, stretchParameters, turn = 0 } = ev.data;
      const image = storedImages.get(imageId);
      if (!image) {
        throw new Error(`No decoded image filed under id ${imageId}`);
      }

      const destinationWidth = Math.round(dstW * dpr);
      const destinationHeight = Math.round(dstH * dpr);

      let parameters: MtfStretchParameters;
      if (stretch && stretchParameters) {
        // The library worked out the stretch for this image; it is applied as sent.
        parameters = shaderParametersFromLibraryStretch(stretchParameters);
      } else if (displayRange) {
        // A caller that knows the file's scale (a stretched picture runs from
        // 0 to 1) passes it, so the picture is not rescaled to its own darkest
        // and brightest pixels.
        parameters = computeLinearStretchParameters(displayRange[0], displayRange[1]);
      } else if (stretch) {
        // A stretch was asked for but none was sent with the image: a linear view
        // over the file's own range, as it always was.
        parameters = computeLinearStretchParameters(image.min, image.max);
      } else {
        // The Linear display of raw data: a straight line between the sky-based
        // black and white points, so a few saturated stars do not set the scale.
        const { low, high } = getSkyRange(image);
        parameters = computeLinearStretchParameters(low, high);
      }

      // GPU path: draw at full source resolution via the shared shader, then
      // let createImageBitmap's own high-quality resize handle the downscale
      // to destination size.
      let bitmap: ImageBitmap;
      // The auto-stretch and the Linear view honour the file's ROWORDER.
      // A picture drawn over a fixed `displayRange` never did (it always drew the
      // first row at the top), and that is kept as is so pictures already shown
      // that way do not suddenly turn over.
      const isTopDownRowOrder = stretch || !displayRange ? image.rowOrder === 'TOP-DOWN' : true;
      image.lastDrawnFirstRowAtTop = isTopDownRowOrder;
      image.lastDrawnTurn = turn;
      const fullResolutionBitmap = renderViaWebGL(imageId, image, parameters, isTopDownRowOrder, turn);
      if (fullResolutionBitmap) {
        bitmap = await createImageBitmap(fullResolutionBitmap, {
          resizeWidth: destinationWidth,
          resizeHeight: destinationHeight,
          resizeQuality: 'high',
        });
        fullResolutionBitmap.close();
      } else {
        // The fallback draws the file's first row at the top whatever the row order.
        image.lastDrawnFirstRowAtTop = true;
        bitmap = await renderViaScalarLoops(image, stretch, parameters, destinationWidth, destinationHeight, turn);
      }

      (self as any).postMessage({ cmd: 'renderComplete', requestId, imageId, bitmap }, [bitmap]);
    } catch (error) {
      (self as any).postMessage({ cmd: 'error', requestId, error: String(error) });
    }
  }
};
