/**
 * @file test_focusStar.test.tsx
 * @description Tests that the viewer puts a given star at the center of the view when a picture is drawn
 * (the Blink comparison's reference star): in a FITS image and in a picture the backend drew, with the
 * start zoom on the first picture and the person's zoom kept on the next one, and that the star is placed
 * correctly in a frame turned half a revolution.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

type Header = Record<string, string | number>;
/** What the stand-in loader reports; each test sets it before rendering. */
const loaderState = {
  parsedData: null as null | { imageId: number; w: number; h: number; header?: Header },
  bitmap: null as null | { width: number; height: number },
  phase: 'ready' as string,
  errorMessage: null as string | null,
  loadedKey: null as null | string,
  workerRef: { current: null as unknown },
};
// The backend turns the west-side frame half a turn to match the east-side stack.
vi.mock('../../services/targetService', () => ({
  fetchFrameDisplayTurn: async () => 180,
}));
vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({ ...loaderState }),
}));
vi.mock('../utils/pixelQuery', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/pixelQuery')>()),
  // The first row of the file is at the bottom of the picture, as in a standard FITS display.
  askWorkerForPixel: async (_worker: unknown, _imageId: number, cell: { column: number; fileRow: number }) => (
    { x: cell.column + 1, y: cell.fileRow + 1, value: 0, hasSky: false }
  ),
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
import type { FocusStar } from '../FitsRenderer';

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 800;
const FILE_WIDTH = 800;
const FILE_HEIGHT = 600;

/**
 * Renders the viewer for one image with a focus star.
 *
 * @param url The image's address, which is its key.
 * @param focusStar The star to center.
 * @returns The element to render.
 */
function viewer(url: string, focusStar: FocusStar | null) {
  return (
    <FitsRenderer
      imageUrl={url} imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="M 81" autoPanTrigger={0} focusStar={focusStar}
    />
  );
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
 * Tells which point of the drawn picture is at the center of the view.
 *
 * @param container The rendered viewer.
 * @param w Width the picture is drawn at.
 * @param h Height the picture is drawn at.
 * @returns The point as fractions of width and height from the top-left corner.
 */
function centerFraction(container: HTMLElement, w: number, h: number) {
  const view = readView(container);
  return {
    x: ((VIEW_WIDTH / 2 - view.panX) / view.zoom) / w,
    y: ((VIEW_HEIGHT / 2 - view.panY) / view.zoom) / h,
  };
}

/**
 * Makes the loader report a decoded FITS image.
 *
 * @param url The image's address.
 * @param header Header cards, if any.
 */
function loadFits(url: string, header?: Header) {
  loaderState.parsedData = { imageId: Math.floor(Math.random() * 1e9), w: FILE_WIDTH, h: FILE_HEIGHT, header };
  loaderState.bitmap = null;
  loaderState.loadedKey = url;
  loaderState.workerRef = { current: { addEventListener: () => undefined, removeEventListener: () => undefined, postMessage: () => undefined } };
}

describe('focus star', () => {
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

  /** The star's place in the file (first row at the bottom) becomes the center of the view, at the start zoom. */
  it('centers the star of a FITS image and zooms to the start zoom', async () => {
    loadFits('/bad.fits');
    const { container } = render(viewer('/bad.fits', { x: 201, y: 151, width: FILE_WIDTH, height: FILE_HEIGHT, key: 'bad', scale: 4 }));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(4, 3));
    const center = centerFraction(container, FILE_WIDTH, FILE_HEIGHT);
    // Column 201 is 200.5 pixels in; row 151 is 150.5 pixels up from the bottom of the picture.
    expect(center.x).toBeCloseTo(200.5 / FILE_WIDTH, 3);
    expect(center.y).toBeCloseTo(1 - 150.5 / FILE_HEIGHT, 3);
  });

  /** Blinking draws the other frame: the person's zoom stays and that frame's star is centered. */
  it('keeps the zoom and centers the next frame\'s star', async () => {
    loadFits('/bad.fits');
    const { container, rerender } = render(viewer('/bad.fits', { x: 201, y: 151, width: FILE_WIDTH, height: FILE_HEIGHT, key: 'bad', scale: 4 }));
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(4, 3));

    loadFits('/typical.fits');
    rerender(viewer('/typical.fits', { x: 211, y: 147, width: FILE_WIDTH, height: FILE_HEIGHT, key: 'typical' }));
    await waitFor(() => {
      const center = centerFraction(container, FILE_WIDTH, FILE_HEIGHT);
      expect(center.x).toBeCloseTo(210.5 / FILE_WIDTH, 3);
    });
    expect(readView(container).zoom).toBeCloseTo(4, 3);
    expect(centerFraction(container, FILE_WIDTH, FILE_HEIGHT).y).toBeCloseTo(1 - 146.5 / FILE_HEIGHT, 3);
  });

  /** A picture the backend drew is smaller than the file: the star is placed by fractions and the zoom counts file pixels. */
  it('centers the star in a picture drawn at another size', async () => {
    loaderState.parsedData = null;
    loaderState.bitmap = { width: 400, height: 300 };
    loaderState.loadedKey = '/picture.png';
    const { container } = render(viewer('/picture.png', { x: 201, y: 151, width: FILE_WIDTH, height: FILE_HEIGHT, key: 'bad', scale: 4 }));
    // 4 screen pixels per file pixel is 8 per picture pixel, because the picture is half the file's size.
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(8, 3));
    const center = centerFraction(container, 400, 300);
    expect(center.x).toBeCloseTo(200.5 / FILE_WIDTH, 3);
    expect(center.y).toBeCloseTo(1 - 150.5 / FILE_HEIGHT, 3);
  });

  /** A frame turned half a revolution shows its star at the mirrored place of the picture, where the other frame has it. */
  it('puts the star of a half-turned frame where the other frame has it on screen', async () => {
    loadFits('/typical.fits', { PIERSIDE: 'WEST' });
    const { container } = render(
      <FitsRenderer
        imageUrl="/typical.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 81" autoPanTrigger={0} matchStackTurn resolveMeasurePath={async () => '/typical.fits'}
        focusStar={{ x: 601, y: 451, width: FILE_WIDTH, height: FILE_HEIGHT, key: 'typical', scale: 4 }}
      />
    );
    await waitFor(() => expect(readView(container).zoom).toBeCloseTo(4, 3));
    const center = centerFraction(container, FILE_WIDTH, FILE_HEIGHT);
    // The frame is turned 180 degrees, so a star at (600.5, 450.5 up) shows at (200 from the left, 150 from the bottom up).
    expect(center.x).toBeCloseTo(1 - 600.5 / FILE_WIDTH, 3);
    expect(center.y).toBeCloseTo(450.5 / FILE_HEIGHT, 3);
  });
});
