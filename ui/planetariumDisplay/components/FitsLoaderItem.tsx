/**
 * @fileoverview FITS image pixel-processing and loading component for the Planetarium display.
 *
 * Handles FITS file decoding, pixel stretching (shadow/midtone/highlight transfer function),
 * and canvas rendering for sky-projection overlays. Extracted from CelestialSkyMap to keep
 * the engine component free of image-processing concerns.
 *
 */

import React, { useEffect, useState } from 'react';
import { PlanetariumTarget } from '../../common/types/planetariumTypes';
import { useFitsLoader } from '../../common/fitsViewer/hooks/useFitsLoader';
import { resolveImageSrc } from '../../common/services/backendApi';
import { renderMtfStretch } from '../../common/fitsViewer/mtfStretchGL';

/**
 * Represents a fully decoded and pixel-stretched FITS entry ready for canvas projection.
 */
export interface LoadedFitsEntry {
  image: HTMLCanvasElement | ImageBitmap;
  crval1?: number;
  crval2?: number;
  cdelt1?: number;
  cdelt2?: number;
  width?: number;
  height?: number;
  roworder?: string;
}

/**
 * Props for FitsLoaderItem.
 */
interface FitsLoaderProps {
  /** The target whose stackedImage path will be decoded. */
  target: PlanetariumTarget;
  /**
   * Callback invoked once the FITS data has been decoded and rendered to an
   * offscreen canvas. The engine stores the result in loadedFits for overlay use.
   */
  onLoaded: (targetId: string, entry: LoadedFitsEntry) => void;
}

/**
 * FitsLoaderItemInternal
 *
 * Loads a FITS image for a single target, applies pixel stretching using a
 * midtone transfer function (MTF), and reports the result via onLoaded.
 * Renders nothing to the DOM — it is a pure data-loading side-effect component.
 *
 * Stretching pipeline:
 *   1. Sample pixels to derive median and stddev.
 *   2. Set shadow floor at median - 2.8σ.
 *   3. Compute midtone parameter to target a 5% background level.
 *   4. Apply MTF per pixel, correcting FITS row order (BOTTOM-UP vs TOP-DOWN).
 *
 */
// Module-level singleton, shared by every FitsLoaderItem instance (one per
// target on the sky map). Targets are stretched sequentially, never
// concurrently, so a single context suffices; giving each target's own
// component instance a persistent context instead (as a useRef would) leaks
// one live WebGL context per stacked-image target for the component's
// lifetime, which can exhaust the page's shared WebGL context budget (the
// Plotly/WebGL widgets elsewhere on the Observatory Manager display included)
// well before any target unmounts to free one.
let sharedMtfCanvas: HTMLCanvasElement | null = null;

/**
 * Lazily creates (once) and returns the shared offscreen canvas used for
 * every target's MTF stretch render pass.
 *
 * @returns {HTMLCanvasElement} The shared offscreen canvas.
 */
function getSharedMtfCanvas(): HTMLCanvasElement {
  if (!sharedMtfCanvas) {
    sharedMtfCanvas = document.createElement('canvas');
  }
  return sharedMtfCanvas;
}

const FitsLoaderItemInternal: React.FC<FitsLoaderProps> = ({ target, onLoaded }) => {
  const [status, setStatus] = useState<string | null>(null);
  const activeFitsUrl = target.stackedImage ? resolveImageSrc(target.stackedImage) : null;
  const { parsedData, bitmap } = useFitsLoader(activeFitsUrl, null, setStatus);

  useEffect(() => {
    let cancelled = false;

    // Fast path: already a hardware-decoded ImageBitmap (browser-supported format).
    if (bitmap) {
      onLoaded(target.id, {
        image: bitmap,
        crval1: parsedData?.crval1,
        crval2: parsedData?.crval2,
        cdelt1: parsedData?.cdelt1,
        cdelt2: parsedData?.cdelt2,
        width: parsedData?.w || bitmap.width,
        height: parsedData?.h || bitmap.height,
        roworder: parsedData?.roworder
      });
      return;
    }
    if (!parsedData) return;

    // Scale down to at most 512px on the longest side to limit GPU memory usage.
    const maxDim = 512;
    const scale = Math.min(1.0, maxDim / Math.max(parsedData.w, parsedData.h));
    const finalW = Math.max(1, Math.round(parsedData.w * scale));
    const finalH = Math.max(1, Math.round(parsedData.h * scale));

    const glCanvas = getSharedMtfCanvas();
    const gl = glCanvas.getContext('webgl2');
    if (!gl) {
      console.error('WebGL2 is not available; cannot render FITS MTF stretch.');
      return;
    }

    renderMtfStretch(gl, {
      raw: parsedData.raw,
      sourceWidth: parsedData.w,
      sourceHeight: parsedData.h,
      channels: parsedData.channels === 3 ? 3 : 1,
      isTopDownRowOrder: parsedData.roworder === 'TOP-DOWN',
      destinationWidth: finalW,
      destinationHeight: finalH
    });

    // createImageBitmap() snapshots the canvas's pixels at this call (per
    // spec, independent of when the returned promise settles), so this
    // target's result survives the shared canvas being redrawn for the next
    // target. Using transferToImageBitmap() instead is not an option here:
    // that detaches the canvas's own backing store, which would break it for
    // every other target still to be rendered this pass.
    createImageBitmap(glCanvas).then(resultBitmap => {
      if (cancelled) {
        resultBitmap.close();
        return;
      }
      onLoaded(target.id, {
        image: resultBitmap,
        crval1: parsedData.crval1,
        crval2: parsedData.crval2,
        cdelt1: parsedData.cdelt1,
        cdelt2: parsedData.cdelt2,
        width: parsedData.w,
        height: parsedData.h,
        roworder: parsedData.roworder
      });
    });

    return () => {
      cancelled = true;
    };
  }, [parsedData, bitmap, target.id, onLoaded]);

  return null;
};

/**
 * FitsLoaderItem
 *
 * Memoized wrapper around FitsLoaderItemInternal. Re-renders only when the
 * target ID, stacked image path, or onLoaded callback reference changes.
 */
export const FitsLoaderItem = React.memo(FitsLoaderItemInternal, (prev, next) => {
  return (
    prev.target.id === next.target.id &&
    prev.target.stackedImage === next.target.stackedImage &&
    prev.onLoaded === next.onLoaded
  );
});
