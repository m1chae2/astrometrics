/**
 * @file test_starShapes.test.tsx
 * @description Tests the Shapes overlay: each line runs along its star's long axis (from +y toward +x, like
 * the star profile cut) and is as long as the stretch, a round star draws nothing, the summary words and the
 * comparison with a clicked star read as intended, and the viewer asks for the shapes only while the overlay
 * is on, draws them in a pan-following layer and shows a plain note for an image with too few stars.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildShapeLinePaths, describeFrameShapes, describeStarAgainstFrame, describeStretch,
} from '../utils/starShapes';
import type { DetectionGeometry } from '../utils/detectionMarkers';
import type { StarShapesMeasurement } from '../../types/backendTypes';

/** A stand-in for the FITS worker that answers each pixel question for a bottom-up file. */
class FakeWorker extends EventTarget {
  /**
   * Answers a pixel question with the 1-based position.
   *
   * @param message The question.
   */
  postMessage(message: Record<string, unknown>): void {
    const column = message.column as number;
    const rowFromTop = message.rowFromTop as number;
    this.dispatchEvent(new MessageEvent('message', {
      data: {
        cmd: 'pixelComplete', requestId: message.requestId, imageId: message.imageId,
        result: { x: column + 1, y: 160 - rowFromTop, values: [1000] },
      },
    }));
  }
}
const fakeWorker = new FakeWorker();
const loaderState = {
  parsedData: null as null | { imageId: number; w: number; h: number; channels: number; min: number; max: number },
  bitmap: null as null | { width: number; height: number },
  loadedKey: null as null | string,
};
vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({ ...loaderState, phase: 'ready', errorMessage: null, workerRef: { current: fakeWorker } }),
}));
vi.mock('../hooks/useCanvasDrawer', async () => {
  const react = await import('react');
  return {
    /** Stand-in drawer: reports the image as drawn at its own size. */
    useCanvasDrawer: (
      _canvas: unknown, _worker: unknown,
      parsedData: { w: number; h: number } | null, _bitmap: unknown,
      setDrawnSize: (size: { w: number; h: number }) => void,
      _stretch?: boolean, _range?: unknown, _parameters?: unknown,
      onDrawn?: (drawn: unknown) => void
    ) => {
      react.useEffect(() => {
        if (parsedData) {
          setDrawnSize({ w: parsedData.w, h: parsedData.h });
          onDrawn?.(parsedData);
        }
      }, [parsedData, setDrawnSize, onDrawn]);
    },
  };
});
const callBackendMock = vi.fn();
vi.mock('../../services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { FitsRenderer } from '../FitsRenderer';

/**
 * Makes an answer from the backend.
 *
 * @param shapes The stars.
 * @returns The measurement.
 */
function makeShapes(shapes: StarShapesMeasurement['shapes']): StarShapesMeasurement {
  return {
    path: '/stacks/M31.fits', width: 200, height: 160, shapes, star_count: shapes.length,
    median_width_px: 2.3, median_elongation: shapes.length > 0 ? 0.08 : null, direction_degrees: 40,
    direction_coherence: 0.7, center_width_px: 2.3, corner_width_px: 2.6, reason: shapes.length > 0 ? '' : 'too few stars',
  };
}

const geometry: DetectionGeometry = {
  imageWidth: 200, imageHeight: 160, stepX: 2, stepY: 2, panX: 0, panY: 0,
  firstRowAtTop: true, viewWidth: 300, viewHeight: 200,
};

/**
 * Reads the two end points of the only segment in a path.
 *
 * @param path The path data.
 * @returns The start and end, in screen pixels.
 */
function endPoints(path: string): { x1: number; y1: number; x2: number; y2: number } {
  const [x1, y1, x2, y2] = (path.match(/-?\d+\.\d/g) ?? []).map(Number);
  return { x1, y1, x2, y2 };
}

describe('shape lines', () => {
  it('runs a 0° line straight up and down and a 90° line left and right', () => {
    const vertical = endPoints(buildShapeLinePaths([{ x: 50, y: 50, width_px: 3, elongation: 0.5, angle_degrees: 0 }], geometry).lines);
    expect(vertical.x1).toBeCloseTo(vertical.x2, 1);
    expect(Math.abs(vertical.y2 - vertical.y1)).toBeCloseTo(40, 0);
    const horizontal = endPoints(buildShapeLinePaths([{ x: 50, y: 50, width_px: 3, elongation: 0.5, angle_degrees: 90 }], geometry).lines);
    expect(horizontal.y1).toBeCloseTo(horizontal.y2, 1);
    expect(Math.abs(horizontal.x2 - horizontal.x1)).toBeCloseTo(40, 0);
  });

  it('tilts a 45° line toward +x and, with the first row at the bottom, toward the top of the screen', () => {
    const star = [{ x: 50, y: 50, width_px: 3, elongation: 0.5, angle_degrees: 45 }];
    const topFirst = endPoints(buildShapeLinePaths(star, geometry).lines);
    expect(topFirst.x2 - topFirst.x1).toBeGreaterThan(0);
    expect(topFirst.y2 - topFirst.y1).toBeGreaterThan(0);
    const bottomFirst = endPoints(buildShapeLinePaths(star, { ...geometry, firstRowAtTop: false }).lines);
    expect(bottomFirst.x2 - bottomFirst.x1).toBeGreaterThan(0);
    expect(bottomFirst.y2 - bottomFirst.y1).toBeLessThan(0);
  });

  it('draws nothing for a round star, caps a long line and leaves out stars out of view', () => {
    const paths = buildShapeLinePaths([
      { x: 20, y: 20, width_px: 3, elongation: 0.01, angle_degrees: 10 },
      { x: 40, y: 30, width_px: 3, elongation: 0.9, angle_degrees: 90 },
      { x: 190, y: 150, width_px: 3, elongation: 0.3, angle_degrees: 10 },
    ], geometry);
    expect(paths.visibleCount).toBe(1);
    const capped = endPoints(paths.lines);
    expect(Math.abs(capped.x2 - capped.x1)).toBeCloseTo(60, 0);
  });
});

describe('shape words', () => {
  it('names a stretch and writes the frame summary', () => {
    expect(describeStretch(0.02)).toBe('round');
    expect(describeStretch(0.1)).toBe('slightly oval');
    expect(describeStretch(0.29)).toBe('oval');
    expect(describeFrameShapes(makeShapes([{ x: 5, y: 5, width_px: 2, elongation: 0.08, angle_degrees: 40 }])))
      .toBe('Median elongation 8% toward 40°; width 2.3 px center, 2.6 px corners');
    expect(describeFrameShapes(makeShapes([]))).toBeNull();
  });

  it('compares a clicked star with the frame, naming the nearest star when it is not the same one', () => {
    const measurement = makeShapes([{ x: 100, y: 80, width_px: 2.5, elongation: 0.29, angle_degrees: 30 }]);
    expect(describeStarAgainstFrame(measurement, 101, 81)).toBe('This star: oval 29%; frame median 8%');
    expect(describeStarAgainstFrame(measurement, 110, 80)).toBe('Nearest measured star (10 px away): oval 29%; frame median 8%');
    expect(describeStarAgainstFrame(makeShapes([]), 1, 1)).toBeNull();
  });
});

describe('Shapes overlay', () => {
  beforeEach(() => {
    callBackendMock.mockReset();
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/M31.fits';
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /**
   * Renders the viewer with the overlay on or off.
   *
   * @param showShapes True to turn the overlay on.
   * @returns The render result.
   */
  function renderViewer(showShapes: boolean) {
    return render(
      <FitsRenderer
        imageUrl="/static/M31.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 31" autoPanTrigger={0} showShapes={showShapes}
        resolveMeasurePath={async () => '/stacks/M31.fits'}
      />
    );
  }

  it('asks for nothing and draws nothing while the overlay is off', async () => {
    renderViewer(false);
    await act(async () => { await Promise.resolve(); });
    expect(callBackendMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('shapes-layer')).toBeNull();
    expect(screen.queryByTestId('shapes-note')).toBeNull();
  });

  it('draws the lines in a pan-following layer and gives the summary', async () => {
    callBackendMock.mockResolvedValue(makeShapes([{ x: 60, y: 80, width_px: 2.5, elongation: 0.3, angle_degrees: 40 }]));
    renderViewer(true);
    await waitFor(() => expect(screen.getByTestId('shapes-note').textContent).toContain('Median elongation 8% toward 40°'));
    expect(callBackendMock).toHaveBeenCalledWith('images:measure_star_shapes', { path: '/stacks/M31.fits' }, expect.anything());
    expect(screen.getByTestId('shapes-layer').getAttribute('data-overlay-layer')).toBe('true');
    expect(screen.getByTestId('shapes-lines').getAttribute('d')).not.toBe('');
  });

  it('shows a plain note, not an error, for an image with too few stars', async () => {
    callBackendMock.mockResolvedValue(makeShapes([]));
    renderViewer(true);
    expect((await screen.findByText('Too few usable stars to show their shapes.'))).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
