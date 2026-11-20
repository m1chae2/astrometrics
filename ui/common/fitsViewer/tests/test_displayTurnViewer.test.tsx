/**
 * @file test_displayTurnViewer.test.tsx
 * @description Tests the viewer with a stack orientation: a raw frame from the other side of the pier is
 * shown turned (with a note, and a drawer told to turn it), a frame that already matches is not, and the
 * zoom and the point at the center of the view are kept when the viewer switches between a turned and an
 * unturned frame.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { StackOrientation } from '../displayTurn';

type Header = Record<string, string | number>;
/** What the stand-in loader reports; each test sets it before rendering. */
const loaderState = {
  parsedData: null as null | { imageId: number; w: number; h: number; header?: Header },
  bitmap: null as null | { width: number; height: number },
  phase: 'ready' as string,
  errorMessage: null as string | null,
  loadedKey: null as null | string,
};
/** The turns the stand-in drawer was asked for. */
const turnsAsked: number[] = [];
vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({ ...loaderState, workerRef: { current: null } }),
}));
vi.mock('../hooks/useCanvasDrawer', async () => {
  const react = await import('react');
  return {
    /** Stand-in drawer: each image is drawn at its own size, swapped for a quarter turn. */
    useCanvasDrawer: (
      _canvas: unknown, _worker: unknown,
      parsedData: { w: number; h: number } | null, _bitmap: unknown,
      setDrawnSize: (size: { w: number; h: number }) => void,
      _stretch?: boolean, _range?: unknown, _parameters?: unknown,
      onDrawn?: (drawn: unknown) => void,
      turn: number = 0
    ) => {
      react.useEffect(() => {
        if (parsedData) {
          turnsAsked.push(turn);
          setDrawnSize(turn === 90 || turn === 270 ? { w: parsedData.h, h: parsedData.w } : { w: parsedData.w, h: parsedData.h });
          onDrawn?.(parsedData);
        }
      }, [parsedData, setDrawnSize, onDrawn, turn]);
    },
  };
});

import { FitsRenderer } from '../FitsRenderer';

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 800;
const STACK: StackOrientation = { upAngleDegrees: 344, pierSide: 'EAST' };
const WEST_FRAME: Header = { ROWORDER: 'TOP-DOWN', CROTA2: 16.2, PIERSIDE: 'WEST' };
const EAST_FRAME: Header = { ROWORDER: 'TOP-DOWN', CROTA2: 196.1, PIERSIDE: 'EAST' };

/**
 * Renders the viewer for one frame.
 *
 * @param url The frame's address, which is its key.
 * @param stack The stack's orientation.
 * @returns The element to render.
 */
function viewer(url: string, stack: StackOrientation | null = STACK) {
  return (
    <FitsRenderer
      imageUrl={url} imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="NGC 7331" autoPanTrigger={0} stackOrientation={stack}
    />
  );
}

/**
 * Makes the loader report a decoded raw frame.
 *
 * @param url The frame's address.
 * @param header The frame's header.
 */
function loadFrame(url: string, header: Header) {
  loaderState.parsedData = { imageId: Math.floor(Math.random() * 1e9), w: 2000, h: 1600, header };
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

describe('raw frames shown turned to match the stack', () => {
  beforeEach(() => {
    turnsAsked.length = 0;
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

  /** The west-side frame is drawn turned half a turn and says so. */
  it('turns the west-side frame and shows the note', async () => {
    loadFrame('/001.fits', WEST_FRAME);
    render(viewer('/001.fits'));
    expect((await screen.findByTestId('turn-note')).textContent).toBe('Shown turned 180° to match the stack');
    expect(turnsAsked[turnsAsked.length - 1]).toBe(180);
  });

  /** The east-side frame matches, so there is no turn and no note. */
  it('leaves the east-side frame and shows no note', async () => {
    loadFrame('/080.fits', EAST_FRAME);
    const { container } = render(viewer('/080.fits'));
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(0));
    expect(turnsAsked[turnsAsked.length - 1]).toBe(0);
    expect(screen.queryByTestId('turn-note')).toBeNull();
  });

  /** No stack means no turn. */
  it('does not turn without a stack', async () => {
    loadFrame('/001.fits', WEST_FRAME);
    const { container } = render(viewer('/001.fits', null));
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(0));
    expect(turnsAsked[turnsAsked.length - 1]).toBe(0);
    expect(screen.queryByTestId('turn-note')).toBeNull();
  });

  /** The zoom and the center are kept when the view switches between a turned and an unturned frame. */
  it('keeps the view across a turned and an unturned frame', async () => {
    loadFrame('/001.fits', WEST_FRAME);
    const { container, rerender } = render(viewer('/001.fits'));
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(0));
    const area = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    await act(async () => {
      for (let step = 0; step < 12; step += 1) fireEvent.wheel(area, { deltaY: -100, clientX: 300, clientY: 250 });
    });
    await waitFor(() => expect(readView(container).zoom).toBeGreaterThan(1));
    const before = readView(container);
    loadFrame('/080.fits', EAST_FRAME);
    rerender(viewer('/080.fits'));
    await waitFor(() => expect(loaderState.loadedKey).toBe('/080.fits'));
    await waitFor(() => expect(turnsAsked[turnsAsked.length - 1]).toBe(0));
    const after = readView(container);
    expect(after.zoom).toBeCloseTo(before.zoom, 6);
    expect(after.panX).toBeCloseTo(before.panX, 3);
    expect(after.panY).toBeCloseTo(before.panY, 3);
  });
});
