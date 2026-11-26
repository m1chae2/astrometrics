/**
 * @file test_restoreView.test.tsx
 * @description Tests that the viewer puts a saved view back (the point at the center and the zoom, as a
 * multiple of the fit), keeps the picture hidden until it is in place, reports its view for saving, and
 * keeps a view the person set by hand when the screen comes back from being hidden.
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
import type { RestoreView } from '../hooks/useViewPlacement';

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 800;
const IMAGE_WIDTH = 2000;
const IMAGE_HEIGHT = 1600;
/** The zoom that fits the whole image in the window, with its 5 percent margin. */
const FIT = Math.min(VIEW_WIDTH / IMAGE_WIDTH, VIEW_HEIGHT / IMAGE_HEIGHT) * 0.95;

/**
 * Renders the viewer.
 *
 * @param restoreView The view to put back, or null.
 * @param onViewChange Called with the view after it changes.
 * @returns The element.
 */
function viewer(restoreView: RestoreView | null, onViewChange?: (view: { x: number; y: number; zoomOverFit: number; turn: number }) => void) {
  return (
    <FitsRenderer
      imageUrl="/a.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="M 42" autoPanTrigger={0} restoreView={restoreView} onViewChange={onViewChange}
    />
  );
}

/**
 * Makes the loader report a decoded image.
 */
function loadImage() {
  loaderState.parsedData = { imageId: 1, w: IMAGE_WIDTH, h: IMAGE_HEIGHT };
  loaderState.bitmap = null;
  loaderState.loadedKey = '/a.fits';
}

/**
 * Reads the zoom and pan the canvas is drawn at.
 *
 * @param container The rendered viewer.
 * @returns The zoom and pan.
 */
function readView(container: HTMLElement) {
  const canvas = container.querySelector('canvas:not([data-overlay-layer])') as HTMLCanvasElement;
  const match = /translate\(([-\d.e]+)px, ([-\d.e]+)px\) scale\(([-\d.e]+)\)/.exec(canvas.style.transform);
  if (!match) throw new Error(`no transform: ${canvas.style.transform}`);
  return { panX: Number(match[1]), panY: Number(match[2]), zoom: Number(match[3]) };
}

/** What the stand-in resize observer calls when the test changes the window's size. */
let resizeCallback: ((entries: Array<{ contentRect: { width: number; height: number } }>) => void) | null = null;

describe('a saved view put back', () => {
  beforeEach(() => {
    loaderState.errorMessage = null;
    loaderState.phase = 'ready';
    loadImage();
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => VIEW_WIDTH });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => VIEW_HEIGHT });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    vi.stubGlobal('ResizeObserver', class {
      /** Keeps the callback so the test can call it. */
      constructor(callback: typeof resizeCallback) { resizeCallback = callback; }
      /** Not needed by the stand-in. */
      observe() {}
      /** Not needed by the stand-in. */
      disconnect() {}
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    resizeCallback = null;
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  });

  it('puts the zoom (as a multiple of the fit) and the center point back', async () => {
    const { container } = render(viewer({ key: 'r1', view: { x: 0.25, y: 0.75, zoomOverFit: 4, turn: 0 } }));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(4 * FIT, 5));
    const view = readView(container);
    expect(((VIEW_WIDTH / 2 - view.panX) / view.zoom) / IMAGE_WIDTH).toBeCloseTo(0.25, 4);
    expect(((VIEW_HEIGHT / 2 - view.panY) / view.zoom) / IMAGE_HEIGHT).toBeCloseTo(0.75, 4);
    expect(container.querySelector('.fits-renderer__canvas-container--restoring')).toBeNull();
  });

  it('keeps the picture hidden until the view is in place', async () => {
    loaderState.parsedData = null;
    loaderState.loadedKey = null;
    const { container, rerender } = render(viewer({ key: 'r1', view: { x: 0.5, y: 0.5, zoomOverFit: 2, turn: 0 } }));
    expect(container.querySelector('.fits-renderer__canvas-container--restoring')).not.toBeNull();
    loadImage();
    rerender(viewer({ key: 'r1', view: { x: 0.5, y: 0.5, zoomOverFit: 2, turn: 0 } }));
    await waitFor(() => expect(container.querySelector('.fits-renderer__canvas-container--restoring')).toBeNull());
    expect(readView(container).zoom).toBeCloseTo(2 * FIT, 5);
  });

  it('reports the view, as a multiple of the fit, once it is in place', async () => {
    const reports: Array<{ x: number; y: number; zoomOverFit: number }> = [];
    render(viewer({ key: 'r1', view: { x: 0.25, y: 0.75, zoomOverFit: 4, turn: 0 } }, (view) => reports.push(view)));
    await waitFor(() => expect(reports.length).toBeGreaterThan(0));
    const last = reports[reports.length - 1];
    expect(last.zoomOverFit).toBeCloseTo(4, 4);
    expect(last.x).toBeCloseTo(0.25, 4);
    expect(last.y).toBeCloseTo(0.75, 4);
  });

  it('keeps a view set by hand when the screen comes back from being hidden', async () => {
    const { container } = render(viewer(null));
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(0));
    const area = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    await act(async () => { for (let step = 0; step < 12; step += 1) fireEvent.wheel(area, { deltaY: -100, clientX: 300, clientY: 250 }); });
    const before = readView(container);
    expect(before.zoom).toBeGreaterThan(FIT);
    act(() => {
      resizeCallback?.([{ contentRect: { width: 0, height: 0 } }]);
      resizeCallback?.([{ contentRect: { width: VIEW_WIDTH, height: VIEW_HEIGHT } }]);
    });
    const after = readView(container);
    expect(after.zoom).toBeCloseTo(before.zoom, 6);
    expect(after.panX).toBeCloseTo(before.panX, 3);
    expect(after.panY).toBeCloseTo(before.panY, 3);
  });
});
