/**
 * @file test_displayTurnBitmap.test.tsx
 * @description Tests the display turn on the pictures the backend already drew (Auto Stretch and the
 * preview): the drawer turns the bitmap by a quarter turn with the same mapping as the FITS path, and
 * the viewer asks the backend for the turn of the file behind the picture, holds the picture until
 * the turn is known, and keeps both the turn and the view when the mode switches between Linear and
 * Auto Stretch.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { baseToTurned } from '../displayTurn';

type Header = Record<string, string | number>;
/** What the stand-in loader reports; each test sets it before rendering. */
const loaderState = {
  parsedData: null as null | { imageId: number; w: number; h: number; header?: Header },
  bitmap: null as null | { width: number; height: number },
  phase: 'ready' as string,
  errorMessage: null as string | null,
  loadedKey: null as null | string,
};
/** What the stand-in drawer was given: the bitmap (or null) and the turn, for each draw. */
const drawCalls: Array<{ bitmap: unknown; turn: number }> = [];
const turnCalls: string[] = [];
let turnForPath: Record<string, number> = {};

vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({ ...loaderState, workerRef: { current: null } }),
}));
vi.mock('../../services/targetService', () => ({
  /** Stand-in turn lookup: the turn the backend gives each path, set by the test. */
  fetchFrameDisplayTurn: async (_target: string, path: string) => {
    turnCalls.push(path);
    return turnForPath[path] ?? 0;
  },
}));
vi.mock('../hooks/useCanvasDrawer', async () => {
  const react = await import('react');
  const actual = await vi.importActual<typeof import('../hooks/useCanvasDrawer')>('../hooks/useCanvasDrawer');
  return {
    setQuarterTurnTransform: actual.setQuarterTurnTransform,
    /** Stand-in drawer: each image is drawn at its own size, swapped for a quarter turn. */
    useCanvasDrawer: (
      _canvas: unknown, _worker: unknown,
      parsedData: { w: number; h: number } | null, bitmap: { width: number; height: number } | null,
      setDrawnSize: (size: { w: number; h: number }) => void,
      _stretch?: boolean, _range?: unknown, _parameters?: unknown,
      onDrawn?: (drawn: unknown) => void,
      turn: number = 0
    ) => {
      react.useEffect(() => {
        const drawn = parsedData ?? bitmap;
        if (!drawn) return;
        const width = 'w' in drawn ? drawn.w : drawn.width;
        const height = 'h' in drawn ? drawn.h : drawn.height;
        drawCalls.push({ bitmap: parsedData ? null : bitmap, turn });
        setDrawnSize(turn === 90 || turn === 270 ? { w: height, h: width } : { w: width, h: height });
        onDrawn?.(drawn);
      }, [parsedData, bitmap, setDrawnSize, onDrawn, turn]);
    },
  };
});

import { FitsRenderer } from '../FitsRenderer';
import { setQuarterTurnTransform } from '../hooks/useCanvasDrawer';

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 800;
const WEST_FRAME: Header = { ROWORDER: 'TOP-DOWN', CROTA2: 16.2, PIERSIDE: 'WEST' };

/**
 * Renders the viewer for one frame.
 *
 * @param key The frame's address, which is its key.
 * @returns The element to render.
 */
function viewer(key: string) {
  const resolve = async () => '/frames/frame-001.fits';
  return (
    <FitsRenderer
      imageUrl={key} imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="NGC 7331" autoPanTrigger={0} matchStackTurn resolveMeasurePath={resolve}
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

describe('the turn of a picture the backend drew', () => {
  /** Each quarter turn moves the picture's corners and centers the way the FITS path does. */
  it('uses a canvas transform that agrees with the point mapping', () => {
    const width = 300;
    const height = 200;
    for (const turn of [90, 180, 270]) {
      const matrix: number[] = [];
      const context = { setTransform: (...values: number[]) => matrix.push(...values) };
      setQuarterTurnTransform(context as unknown as CanvasRenderingContext2D, width, height, turn);
      const [a, b, c, d, e, f] = matrix;
      for (const point of [{ x: 0, y: 0 }, { x: 10, y: 20 }, { x: width, y: height }, { x: 150, y: 30 }]) {
        const expected = baseToTurned(point, width, height, turn);
        expect(a * point.x + c * point.y + e).toBeCloseTo(expected.x, 9);
        expect(b * point.x + d * point.y + f).toBeCloseTo(expected.y, 9);
      }
    }
  });
});

describe('the bitmap drawer with a turn', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** A half turn keeps the size, a quarter turn swaps it, and the bitmap is drawn through the turning transform. */
  it('sizes the canvas for the turned picture and draws through the transform', async () => {
    const { useCanvasDrawer: realDrawer } = await vi.importActual<typeof import('../hooks/useCanvasDrawer')>('../hooks/useCanvasDrawer');
    for (const [turn, w, h] of [[180, 300, 200], [90, 200, 300], [270, 200, 300]] as const) {
      const canvas = document.createElement('canvas');
      const context = { setTransform: vi.fn(), clearRect: vi.fn(), drawImage: vi.fn() };
      vi.spyOn(canvas, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
      const bitmap = { width: 300, height: 200, close: vi.fn() } as unknown as ImageBitmap;
      const setDrawnSize = vi.fn();
      renderHook(() => realDrawer({ current: canvas }, { current: null }, null, bitmap, setDrawnSize, true, undefined, undefined, undefined, turn));
      expect([canvas.width, canvas.height]).toEqual([w, h]);
      expect(context.drawImage).toHaveBeenCalledWith(bitmap, 0, 0);
      expect(setDrawnSize).toHaveBeenCalledWith({ w, h });
    }
  });
});

describe('Auto Stretch pictures shown turned to match the stack', () => {
  beforeEach(() => {
    drawCalls.length = 0;
    turnCalls.length = 0;
    // Frame 001 of NGC 7331 is from the west side of the pier: the backend turns it half a turn.
    turnForPath = { '/frames/frame-001.fits': 180 };
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

  /** The bitmap of a west-side frame is drawn turned half a turn, with the note, and only once the turn is known. */
  it('turns a frame-001 bitmap by 180 degrees and shows the note', async () => {
    loaderState.parsedData = null;
    loaderState.bitmap = { width: 2000, height: 1600 };
    loaderState.loadedKey = '/auto-001.jpg';
    render(viewer('/auto-001.jpg'));
    expect((await screen.findByTestId('turn-note')).textContent).toBe('Shown turned 180° to match the stack');
    expect(turnCalls).toEqual(['/frames/frame-001.fits']);
    const draws = drawCalls.filter((call) => call.bitmap !== null);
    expect(draws.length).toBeGreaterThan(0);
    // Nothing was drawn before the turn was known: every draw of the bitmap is turned.
    expect(draws.every((call) => call.turn === 180)).toBe(true);
  });

  /** Switching from Linear to Auto Stretch on a turned frame keeps the turn and the view. */
  it('keeps the orientation and the view when the mode switches', async () => {
    loaderState.parsedData = { imageId: 1, w: 2000, h: 1600, header: WEST_FRAME };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/001.fits';
    const { container, rerender } = render(viewer('/001.fits'));
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(0));
    const area = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    await act(async () => {
      for (let step = 0; step < 12; step += 1) fireEvent.wheel(area, { deltaY: -100, clientX: 300, clientY: 250 });
    });
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(1));
    const before = readView(container);

    loaderState.parsedData = null;
    loaderState.bitmap = { width: 2000, height: 1600 };
    loaderState.loadedKey = '/auto-001.jpg';
    rerender(viewer('/auto-001.jpg'));
    await waitFor(() => expect(drawCalls.some((call) => call.bitmap !== null)).toBe(true));
    expect(drawCalls[drawCalls.length - 1].turn).toBe(180);
    expect(screen.getByTestId('turn-note')).toBeTruthy();
    const after = readView(container);
    expect(after.zoom).toBeCloseTo(before.zoom, 6);
    expect(after.panX).toBeCloseTo(before.panX, 3);
    expect(after.panY).toBeCloseTo(before.panY, 3);
  });
});
