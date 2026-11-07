/**
 * @file test_previewFirst.test.tsx
 * @description Tests for what the viewer shows while a picture loads: a spinner
 * (and no empty canvas) until something is really drawn, the small preview first
 * when there is one, and star overlays that wait for the full FITS image.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

/** What the stand-in loader reports; each test sets it before rendering. */
const loaderState = {
  parsedData: null as null | { imageId: number; w: number; h: number },
  bitmap: null as null | ImageBitmap,
  phase: 'parsing' as string,
  errorMessage: null as string | null,
  loadedKey: null as null | string,
};
vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({ ...loaderState, workerRef: { current: null } }),
}));
vi.mock('../hooks/useCanvasDrawer', async () => {
  const react = await import('react');
  return {
    /** Stand-in drawer: a FITS image is drawn 2000 wide, a bitmap at its own size. */
    useCanvasDrawer: (
      _canvas: unknown, _worker: unknown,
      parsedData: { w: number; h: number } | null, bitmap: ImageBitmap | null,
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

const STARS = [{
  id: 'S1', name: 'Star 1', x: 10, y: 10, spectralType: 'G2V', isCatalogIdentified: true,
  referenceWidth: 2000, referenceHeight: 2000,
}];

/**
 * Renders the viewer for a FITS file with optional preview.
 *
 * @param extra Props to change for this render.
 * @returns The element to render.
 */
function viewer(extra: Record<string, unknown> = {}) {
  return (
    <FitsRenderer
      imageUrl="/static/M42_processed.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="M 42" autoPanTrigger={0} overlayStars={STARS} showOverlay {...extra}
    />
  );
}

describe('FitsRenderer first frame', () => {
  beforeEach(() => {
    loaderState.parsedData = null;
    loaderState.bitmap = null;
    loaderState.phase = 'parsing';
    loaderState.errorMessage = null;
    loaderState.loadedKey = null;
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 500, height: 500, close: vi.fn() })));
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** While the FITS file is parsed, the spinner shows, the canvas is hidden and no status words appear. */
  it('shows the spinner and hides the canvas while the FITS file is parsed', () => {
    const { container } = render(viewer());
    expect(container.querySelector('.fits-renderer__loading-overlay')).not.toBeNull();
    expect(container.querySelector('.fits-renderer__canvas--hidden')).not.toBeNull();
    expect(container.textContent).not.toContain('Parsing');
  });

  /** An error stays visible and replaces the spinner. */
  it('shows the error, not the spinner, when loading failed', () => {
    loaderState.phase = 'error';
    loaderState.errorMessage = 'Error: bad pixels';
    const { container } = render(viewer());
    expect(container.querySelector('.fits-renderer__loading-overlay')).toBeNull();
    expect(container.textContent).toContain('Error: bad pixels');
  });

  /** With a preview, it is drawn at once: no spinner, canvas visible, but no star overlay yet. */
  it('draws the preview first and keeps overlays off it', async () => {
    const { container } = render(viewer({ previewBlob: new Blob(['jpeg'], { type: 'image/jpeg' }) }));
    await waitFor(() => expect(container.querySelector('.fits-renderer__loading-overlay')).toBeNull());
    expect(container.querySelector('.fits-renderer__canvas--hidden')).toBeNull();
    expect(container.querySelector('[data-overlay-layer]')).toBeNull();
  });

  /** When the FITS image is ready it replaces the preview and the overlay appears. */
  it('replaces the preview with the FITS image and then shows the overlay', async () => {
    const preview = new Blob(['jpeg'], { type: 'image/jpeg' });
    const { container, rerender } = render(viewer({ previewBlob: preview }));
    await waitFor(() => expect(container.querySelector('.fits-renderer__loading-overlay')).toBeNull());
    expect(container.querySelector('[data-overlay-layer]')).toBeNull();

    loaderState.parsedData = { imageId: 1, w: 2000, h: 2000 };
    loaderState.phase = 'ready';
    loaderState.loadedKey = '/static/M42_processed.fits';
    rerender(viewer({ previewBlob: preview, selectedStarId: 'S1' }));

    await waitFor(() => expect(container.querySelector('[data-overlay-layer]')).not.toBeNull());
    expect(container.querySelector('.fits-renderer__canvas--hidden')).toBeNull();
    expect(container.querySelector('.fits-renderer__loading-overlay')).toBeNull();
  });

  /** While only the preview is on screen, a bar says the full image is still loading; it goes away after. */
  it('shows an updating bar while the preview waits for the full image', async () => {
    const preview = new Blob(['jpeg'], { type: 'image/jpeg' });
    const { container, rerender } = render(viewer({ previewBlob: preview }));
    await waitFor(() => expect(container.querySelector('.fits-renderer__loading-overlay')).toBeNull());
    expect(container.querySelector('[aria-label="Loading the full image"]')).not.toBeNull();

    loaderState.parsedData = { imageId: 1, w: 2000, h: 2000 };
    loaderState.phase = 'ready';
    loaderState.loadedKey = '/static/M42_processed.fits';
    rerender(viewer({ previewBlob: preview, selectedStarId: 'S1' }));
    await waitFor(() => expect(container.querySelector('[aria-label="Loading the full image"]')).toBeNull());
  });
});
