/**
 * @file test_viewKeptOnStretchChange.test.tsx
 * @description Tests that changing the display stretch (Auto Stretch to Linear and back) keeps the
 * zoom and the part of the image in view, even though the loader empties for a moment while the
 * other form of the image loads, and that a new file of another target still fits.
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


/** Renders the viewer for a picture the backend drew (Auto Stretch) or for the FITS file itself (Linear). */
function viewerFor(mode: 'auto' | 'linear', target = 'M 42', file = 'a', loading = false) {
  const blob = mode === 'linear' ? new Blob(['x'], { type: 'application/fits' }) : null;
  return (
    <FitsRenderer
      imageUrl={mode === 'auto' ? `data:image/png;base64,${file}` : null} imageBlob={mode === 'linear' ? blobFor(file, blob) : null}
      frameInfo={null} loading={loading} error={null} stretch={mode === 'auto'}
      selectedTarget={target} autoPanTrigger={1}
    />
  );
}
const blobs = new Map<string, Blob>();
/** Gives the same Blob object for the same file, as the app keeps the file it read. */
function blobFor(file: string, blob: Blob | null) {
  if (blob && !blobs.has(file)) blobs.set(file, blob);
  if (!blobs.has(file)) return null;
  return blobs.get(file) as Blob;
}

/** The image point at the center of the view, in image pixels of a picture drawn at the given width. */
function centerInImagePixels(container: HTMLElement, drawnWidth: number, imageWidth: number) {
  const view = readView(container);
  const toImage = imageWidth / drawnWidth;
  return {
    x: ((VIEW_WIDTH / 2 - view.panX) / view.zoom) * toImage,
    y: ((VIEW_HEIGHT / 2 - view.panY) / view.zoom) * toImage,
    onScreenWidth: view.zoom * drawnWidth,
  };
}

describe('view kept when the display stretch changes', () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => VIEW_WIDTH });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => VIEW_HEIGHT });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    loaderState.phase = 'ready';
    loaderState.errorMessage = null;
  });
  afterEach(() => {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  });

  /** Shows the picture the backend drew (1000 wide), zoomed in by hand. */
  async function startOnAutoStretch() {
    loaderState.parsedData = null;
    loaderState.bitmap = { width: 1000, height: 800 };
    loaderState.loadedKey = 'data:image/png;base64,a';
    const rendered = render(viewerFor('auto'));
    await waitFor(() => expect(readView(rendered.container).zoom).toBeGreaterThan(0));
    const area = rendered.container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    await act(async () => {
      for (let turn = 0; turn < 12; turn += 1) fireEvent.wheel(area, { deltaY: -100, clientX: 300, clientY: 250 });
    });
    await waitFor(() => expect(readView(rendered.container).zoom).toBeGreaterThan(1));
    return rendered;
  }

  /** Auto Stretch to Linear: the same zoom and the same image point at the center, with the loader empty in between. */
  it('keeps the zoom and the center going from Auto Stretch to Linear', async () => {
    const { container, rerender } = await startOnAutoStretch();
    const before = centerInImagePixels(container, 1000, 2000);
    const widthBefore = before.onScreenWidth;
    // The loader empties while the FITS file is read and decoded.
    loaderState.parsedData = null;
    loaderState.bitmap = null;
    loaderState.loadedKey = null;
    rerender(viewerFor('linear', 'M 42', 'a', true));
    loaderState.parsedData = { imageId: 7, w: 2000, h: 1600 };
    loaderState.loadedKey = blobFor('a', null) as unknown as string;
    rerender(viewerFor('linear', 'M 42', 'a'));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(before.onScreenWidth / 2000, 4));
    const after = centerInImagePixels(container, 2000, 2000);
    expect(after.onScreenWidth).toBeCloseTo(widthBefore, 1);
    expect(after.x).toBeCloseTo(before.x, 1);
    expect(after.y).toBeCloseTo(before.y, 1);
  });

  /** A raised fit trigger (a new target picked) still fits the view. */
  it('fits when the fit trigger is raised', async () => {
    const { container, rerender } = await startOnAutoStretch();
    rerender(<FitsRenderer imageUrl="data:image/png;base64,a" imageBlob={null} frameInfo={null} loading={false} error={null}
      stretch selectedTarget="M 42" autoPanTrigger={2} />);
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(Math.min(VIEW_WIDTH / 1000, VIEW_HEIGHT / 800) * 0.95, 6));
  });

  /** A new file of another target still starts from the fit. */
  it('fits a new file of another target', async () => {
    const { container, rerender } = await startOnAutoStretch();
    loaderState.parsedData = null;
    loaderState.bitmap = { width: 1000, height: 800 };
    loaderState.loadedKey = 'data:image/png;base64,c';
    rerender(viewerFor('auto', 'M 43', 'c'));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(Math.min(VIEW_WIDTH / 1000, VIEW_HEIGHT / 800) * 0.95, 6));
  });
});
