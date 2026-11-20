/**
 * @file test_viewKeptAcrossImages.test.tsx
 * @description Tests that the viewer keeps its zoom and the point at the center of the view when
 * another image of the same target is drawn (a new display mode, a raw frame, a stack), matches
 * the sky position when both images have a plate solution, and fits again for a new target.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor, fireEvent, act } from '@testing-library/react';

type Header = Record<string, string | number>;
/** What the stand-in loader reports; each test sets it before rendering. */
const loaderState = {
  parsedData: null as null | { imageId: number; w: number; h: number; header?: Header },
  bitmap: null as null | { width: number; height: number },
  phase: 'ready' as string,
  errorMessage: null as string | null,
  loadedKey: null as null | string,
};
vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({ ...loaderState, workerRef: { current: null } }),
}));
vi.mock('../hooks/useCanvasDrawer', async () => {
  const react = await import('react');
  return {
    /** Stand-in drawer: each image is drawn at its own size. */
    useCanvasDrawer: (
      _canvas: unknown, _worker: unknown,
      parsedData: { w: number; h: number } | null, bitmap: { width: number; height: number } | null,
      setDrawnSize: (size: { w: number; h: number }) => void,
      _stretch?: boolean, _range?: unknown, _parameters?: unknown,
      onDrawn?: (drawn: unknown) => void
    ) => {
      react.useEffect(() => {
        if (parsedData) {
          setDrawnSize({ w: parsedData.w, h: parsedData.h });
          onDrawn?.(parsedData);
        } else if (bitmap) {
          setDrawnSize({ w: bitmap.width, h: bitmap.height });
          onDrawn?.(bitmap);
        }
      }, [parsedData, bitmap, setDrawnSize, onDrawn]);
    },
  };
});

import { FitsRenderer } from '../FitsRenderer';

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 800;

/** A plate solution whose reference pixel is `referenceX`, `referenceY` (about 1.8 arcseconds per pixel). */
const solved = (referenceX: number, referenceY: number): Header => ({
  PLTSOLVD: 1, CTYPE1: 'RA---TAN', CTYPE2: 'DEC--TAN', CRPIX1: referenceX, CRPIX2: referenceY,
  CRVAL1: 150, CRVAL2: 20, CD1_1: -0.0005, CD1_2: 0, CD2_1: 0, CD2_2: 0.0005,
});

/**
 * Renders the viewer for one image.
 *
 * @param url The image's address, which is its key.
 * @param target The selected target.
 * @returns The element to render.
 */
function viewer(url: string, target = 'M 42') {
  return (
    <FitsRenderer
      imageUrl={url} imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget={target} autoPanTrigger={0}
    />
  );
}

/**
 * Makes the loader report a decoded FITS image.
 *
 * @param url The image's address.
 * @param w Width in pixels.
 * @param h Height in pixels.
 * @param header Header cards, if any.
 */
function loadFits(url: string, w: number, h: number, header?: Header) {
  loaderState.parsedData = { imageId: Math.floor(Math.random() * 1e9), w, h, header };
  loaderState.bitmap = null;
  loaderState.loadedKey = url;
}

/**
 * Reads the zoom and pan the image canvas is drawn at.
 *
 * @param container The rendered viewer.
 * @returns The zoom and the pan in screen pixels.
 */
function readView(container: HTMLElement) {
  const canvas = container.querySelector('canvas:not([data-overlay-layer])') as HTMLCanvasElement;
  const match = /translate\(([-\d.e]+)px, ([-\d.e]+)px\) scale\(([-\d.e]+)\)/.exec(canvas.style.transform);
  if (!match) throw new Error(`no transform: ${canvas.style.transform}`);
  return { panX: Number(match[1]), panY: Number(match[2]), zoom: Number(match[3]) };
}

/**
 * Tells which point of an image of a given size is at the center of the view.
 *
 * @param container The rendered viewer.
 * @param w Width the image is drawn at.
 * @param h Height the image is drawn at.
 * @returns The point as fractions of width and height.
 */
function centerFraction(container: HTMLElement, w: number, h: number) {
  const view = readView(container);
  return {
    x: ((VIEW_WIDTH / 2 - view.panX) / view.zoom) / w,
    y: ((VIEW_HEIGHT / 2 - view.panY) / view.zoom) / h,
  };
}

/**
 * Zooms in with the wheel at an off-center point, which also moves the center of the view.
 *
 * @param container The rendered viewer.
 */
function zoomInByHand(container: HTMLElement) {
  const area = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
  for (let turn = 0; turn < 12; turn += 1) {
    fireEvent.wheel(area, { deltaY: -100, clientX: 300, clientY: 250 });
  }
}

describe('view kept across images of one target', () => {
  beforeEach(() => {
    loaderState.errorMessage = null;
    loaderState.phase = 'ready';
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => VIEW_WIDTH });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => VIEW_HEIGHT });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  });

  /**
   * Renders an image of the target and zooms in by hand.
   *
   * @param w Width in pixels.
   * @param h Height in pixels.
   * @param header Header cards, if any.
   * @returns The rendered viewer and the view after the zoom.
   */
  async function startZoomed(w: number, h: number, header?: Header) {
    loadFits('/a.fits', w, h, header);
    const rendered = render(viewer('/a.fits'));
    await waitFor(() => expect(readView(rendered.container).zoom).toBeGreaterThan(0));
    await act(async () => { zoomInByHand(rendered.container); });
    await waitFor(() => expect(readView(rendered.container).zoom).toBeGreaterThan(1));
    return { ...rendered, before: readView(rendered.container) };
  }

  /** An image of the same size keeps the same zoom and pan. */
  it('keeps the view for another image of the same size', async () => {
    const { container, rerender, before } = await startZoomed(2000, 1600);
    loadFits('/b.fits', 2000, 1600);
    rerender(viewer('/b.fits'));
    await waitFor(() => expect(loaderState.loadedKey).toBe('/b.fits'));
    const after = readView(container);
    expect(after.zoom).toBeCloseTo(before.zoom, 6);
    expect(after.panX).toBeCloseTo(before.panX, 3);
    expect(after.panY).toBeCloseTo(before.panY, 3);
  });

  /** A display-mode change swaps the FITS image for a picture of another size: the same part of the image stays at the center. */
  it('keeps the center when the display mode swaps the image for a smaller picture', async () => {
    const { container, rerender, before } = await startZoomed(2000, 1600);
    const centerBefore = centerFraction(container, 2000, 1600);
    loaderState.parsedData = null;
    loaderState.bitmap = { width: 1000, height: 800 };
    loaderState.loadedKey = '/b.png';
    rerender(viewer('/b.png'));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(before.zoom * 2, 4));
    const centerAfter = centerFraction(container, 1000, 800);
    expect(centerAfter.x).toBeCloseTo(centerBefore.x, 4);
    expect(centerAfter.y).toBeCloseTo(centerBefore.y, 4);
  });

  /** An image of another shape keeps the relative position at the center. */
  it('keeps the relative center for an image of another size and shape', async () => {
    const { container, rerender, before } = await startZoomed(2000, 1600);
    const centerBefore = centerFraction(container, 2000, 1600);
    loadFits('/b.fits', 1500, 1500);
    rerender(viewer('/b.fits'));
    await waitFor(() => expect(readView(container).zoom).not.toBeCloseTo(before.zoom, 3));
    const centerAfter = centerFraction(container, 1500, 1500);
    expect(centerAfter.x).toBeCloseTo(centerBefore.x, 4);
    expect(centerAfter.y).toBeCloseTo(centerBefore.y, 4);
  });

  /** With a plate solution in both images, the same sky position stays at the center. */
  it('keeps the sky position at the center when both images are plate solved', async () => {
    const { container, rerender } = await startZoomed(2000, 1600, solved(1000, 800));
    const centerBefore = centerFraction(container, 2000, 1600);
    // The second image's sky map is shifted by 100 pixels: the same star is 100 pixels to the right.
    loadFits('/b.fits', 2000, 1600, solved(1100, 800));
    rerender(viewer('/b.fits'));
    await waitFor(() => expect(centerFraction(container, 2000, 1600).x).not.toBeCloseTo(centerBefore.x, 3));
    const centerAfter = centerFraction(container, 2000, 1600);
    expect(centerAfter.x).toBeCloseTo(centerBefore.x + 100 / 2000, 3);
    expect(centerAfter.y).toBeCloseTo(centerBefore.y, 3);
  });

  /** A new target starts from the fit: the whole image in view, centered. */
  it('fits again when the target changes', async () => {
    const { container, rerender } = await startZoomed(2000, 1600);
    loadFits('/c.fits', 2000, 1600);
    rerender(viewer('/c.fits', 'M 43'));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(Math.min(VIEW_WIDTH / 2000, VIEW_HEIGHT / 1600) * 0.95, 6));
    const view = readView(container);
    expect(view.panX).toBeCloseTo((VIEW_WIDTH - 2000 * view.zoom) / 2, 3);
    expect(view.panY).toBeCloseTo((VIEW_HEIGHT - 1600 * view.zoom) / 2, 3);
  });
});
