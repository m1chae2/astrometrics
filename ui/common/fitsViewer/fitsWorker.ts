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

/** One decoded image held by the worker. */
interface StoredImage {
  pixels: Float32Array;
  width: number;
  height: number;
  channels: number;
  rowOrder: string | undefined;
  min: number;
  max: number;
}

const storedImages = new Map<number, StoredImage>();

let glCanvas: OffscreenCanvas | null = null;
let glContext: WebGL2RenderingContext | null = null;

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
 * @returns A full-resolution bitmap, or null if the GPU path is unavailable (use the JS fallback).
 */
function renderViaWebGL(
  imageId: number,
  image: StoredImage,
  parameters: MtfStretchParameters,
  isTopDownRowOrder: boolean,
): ImageBitmap | null {
  const context = getGlContext();
  if (!context || !glCanvas) return null;
  try {
    renderMtfStretch(context, {
      raw: image.pixels,
      sourceWidth: image.width,
      sourceHeight: image.height,
      channels: image.channels === 3 ? 3 : 1,
      isTopDownRowOrder,
      destinationWidth: image.width,
      destinationHeight: image.height,
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
 * @returns The bitmap, resized to the destination size.
 */
async function renderViaScalarLoops(
  image: StoredImage,
  stretch: boolean,
  parameters: MtfStretchParameters,
  destinationWidth: number,
  destinationHeight: number,
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

  const imageData = new ImageData(pixels, w, h);
  return createImageBitmap(imageData, 0, 0, w, h, {
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
      const { dstW, dstH, dpr, stretch = true, displayRange, stretchParameters } = ev.data;
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
      } else {
        // No stretch asked for, or none sent with the image: a linear view.
        // A caller that knows the file's scale (a stretched picture runs from
        // 0 to 1) passes it, so the picture is not rescaled to its own darkest
        // and brightest pixels. Otherwise the file's own range is used.
        const rangeMinimum = displayRange ? displayRange[0] : image.min;
        const rangeMaximum = displayRange ? displayRange[1] : image.max;
        parameters = computeLinearStretchParameters(rangeMinimum, rangeMaximum);
      }

      // GPU path: draw at full source resolution via the shared shader, then
      // let createImageBitmap's own high-quality resize handle the downscale
      // to destination size.
      let bitmap: ImageBitmap;
      // The auto-stretch honours the file's ROWORDER. The linear view never
      // did (it always drew the first row at the top), and that is kept as is
      // so images already shown unstretched do not suddenly turn over.
      const isTopDownRowOrder = stretch ? image.rowOrder === 'TOP-DOWN' : true;
      const fullResolutionBitmap = renderViaWebGL(imageId, image, parameters, isTopDownRowOrder);
      if (fullResolutionBitmap) {
        bitmap = await createImageBitmap(fullResolutionBitmap, {
          resizeWidth: destinationWidth,
          resizeHeight: destinationHeight,
          resizeQuality: 'high',
        });
        fullResolutionBitmap.close();
      } else {
        bitmap = await renderViaScalarLoops(image, stretch, parameters, destinationWidth, destinationHeight);
      }

      (self as any).postMessage({ cmd: 'renderComplete', requestId, imageId, bitmap }, [bitmap]);
    } catch (error) {
      (self as any).postMessage({ cmd: 'error', requestId, error: String(error) });
    }
  }
};
