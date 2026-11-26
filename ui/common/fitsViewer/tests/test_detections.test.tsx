/**
 * @file test_detections.test.tsx
 * @description Tests the Detections overlay: the markers are placed like the star profile marker (so they
 * follow pan and zoom), single stars and merged stars are different shapes, only markers in view are drawn,
 * the viewer shows "Finding stars…" and then the count, and an image the backend refuses shows nothing.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BackendError } from '../../services/backendError';
import {
  buildDetectionPaths, detectionScreenPosition, CROWDED_DOT_OPACITY, CROWDED_DOT_RADIUS_PX,
  CROWDED_MERGED_DOT_OPACITY, CROWDED_MERGED_DOT_RADIUS_PX, DOT_OPACITY,
} from '../utils/detectionMarkers';
import type { DetectionGeometry } from '../utils/detectionMarkers';
import type { DetectionsMeasurement } from '../../types/backendTypes';

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
 * @param detections The stars.
 * @returns The measurement.
 */
function makeDetections(detections: DetectionsMeasurement['detections']): DetectionsMeasurement {
  return {
    path: '/stacks/M31.fits', width: 200, height: 160, detections,
    detection_count: detections.length, merged_count: detections.filter((item) => item.merged).length,
    kernel_fwhm_px: 4, binned_on_color: false,
  };
}

const geometry: DetectionGeometry = {
  imageWidth: 200, imageHeight: 160, stepX: 2, stepY: 2, panX: 10, panY: 20,
  firstRowAtTop: true, viewWidth: 300, viewHeight: 200,
};

describe('detection marker placement', () => {
  it('puts a 1-based pixel at the center of its pixel on screen, like the star profile marker', () => {
    expect(detectionScreenPosition({ x: 51, y: 120 }, geometry)).toEqual({ x: 10 + 50.5 * 2, y: 20 + 119.5 * 2 });
  });

  it('counts rows from the bottom when the first row is at the bottom', () => {
    const position = detectionScreenPosition({ x: 1, y: 1 }, { ...geometry, firstRowAtTop: false });
    expect(position.y).toBe(20 + 160 * 2 - 1);
  });

  it('draws a single star as a circle and a merged star as a diamond, and leaves out stars out of view', () => {
    const paths = buildDetectionPaths([
      { x: 20, y: 20, flux: 1, merged: false },
      { x: 40, y: 30, flux: 2, merged: true },
      { x: 190, y: 150, flux: 3, merged: false },
    ], geometry);
    expect(paths.circles.match(/a/g)).toHaveLength(2);
    expect(paths.diamonds.match(/Z/g)).toHaveLength(1);
    // The third star is at x = 10 + 189.5 * 2 = 389: outside the 300 px view.
    expect(paths.visibleCount).toBe(2);
  });

  it('draws the photometry circle at its size in image pixels, scaled by the zoom', () => {
    const paths = buildDetectionPaths([{ x: 20, y: 20, flux: 1, merged: false }], { ...geometry, apertureRadiusPx: 4 });
    // 4 image pixels at 2 screen pixels each: an 8 px radius.
    expect(paths.circles).toContain('a8.0 8.0');
  });

  it('draws a dot for every star, single or merged, when the circle would be under 3 screen pixels', () => {
    const paths = buildDetectionPaths(
      [{ x: 20, y: 20, flux: 1, merged: false }, { x: 40, y: 30, flux: 2, merged: true }, { x: 60, y: 30, flux: 2, merged: false }],
      { ...geometry, stepX: 0.5, stepY: 0.5, apertureRadiusPx: 4 },
    );
    expect(paths.mode).toBe('dots');
    expect(paths.circles.match(/a1.25 1.25/g)).toHaveLength(4);
    expect(paths.diamonds.match(/a2 2/g)).toHaveLength(2);
    expect(paths.diamonds).not.toContain('Z');
    expect(paths.visibleCount).toBe(3);
  });

  it('keeps the normal dot size and opacity at low density', () => {
    const paths = buildDetectionPaths(
      [{ x: 20, y: 20, flux: 1, merged: false }],
      { ...geometry, stepX: 0.5, stepY: 0.5, apertureRadiusPx: 4 },
    );
    expect(paths.dotOpacity).toBe(DOT_OPACITY);
  });

  it('shrinks and fades dots in a crowded view, never below the floors, and keeps merged dots larger', () => {
    const many = Array.from({ length: 12000 }, (_, index) => ({
      x: 1 + (index % 200), y: 1 + Math.floor(index / 200) % 160, flux: 1, merged: index % 10 === 0,
    }));
    const paths = buildDetectionPaths(many, {
      ...geometry, stepX: 0.1, stepY: 0.1, panX: 0, panY: 0, viewWidth: 180, viewHeight: 180, apertureRadiusPx: 4,
    });
    expect(paths.mode).toBe('dots');
    expect(paths.dotOpacity).toBeGreaterThanOrEqual(CROWDED_DOT_OPACITY);
    expect(paths.dotOpacity).toBeLessThan(DOT_OPACITY);
    expect(paths.dotOpacity).toBeCloseTo(CROWDED_DOT_OPACITY, 5);
    expect(paths.mergedDotOpacity).toBeCloseTo(CROWDED_MERGED_DOT_OPACITY, 5);
    expect(paths.mergedDotOpacity).toBeGreaterThan(paths.dotOpacity);
    expect(paths.visibleCount).toBe(12000);
    expect(paths.circles).toContain(`a${CROWDED_DOT_RADIUS_PX} ${CROWDED_DOT_RADIUS_PX}`);
    expect(paths.diamonds).toContain(`a${CROWDED_MERGED_DOT_RADIUS_PX} ${CROWDED_MERGED_DOT_RADIUS_PX}`);
  });

  it('draws circles and diamonds once the circle is at least 3 screen pixels', () => {
    const paths = buildDetectionPaths(
      [{ x: 20, y: 20, flux: 1, merged: false }, { x: 40, y: 30, flux: 2, merged: true }],
      { ...geometry, stepX: 0.75, stepY: 0.75, apertureRadiusPx: 4 },
    );
    expect(paths.mode).toBe('circles');
    expect(paths.circles).toContain('a3.0 3.0');
    expect(paths.diamonds).toContain('Z');
  });
});

describe('Detections overlay', () => {
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
   * @param showDetections True to turn the overlay on.
   * @returns The render result.
   */
  function renderViewer(showDetections: boolean) {
    return render(
      <FitsRenderer
        imageUrl="/static/M31.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 31" autoPanTrigger={0} showDetections={showDetections}
        resolveMeasurePath={async () => '/stacks/M31.fits'}
      />
    );
  }

  it('asks for nothing and draws nothing while the overlay is off', async () => {
    renderViewer(false);
    await act(async () => { await Promise.resolve(); });
    expect(callBackendMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('detections-layer')).toBeNull();
    expect(screen.queryByTestId('detections-note')).toBeNull();
  });

  it('says it is finding stars, then draws both shapes and gives the count', async () => {
    let answer: (value: DetectionsMeasurement) => void = () => undefined;
    callBackendMock.mockReturnValue(new Promise<DetectionsMeasurement>((resolve) => { answer = resolve; }));
    renderViewer(true);
    expect((await screen.findByTestId('detections-note')).textContent).toBe('Finding stars…');
    await act(async () => {
      answer(makeDetections([
        { x: 30, y: 40, flux: 5, merged: false },
        { x: 60, y: 80, flux: 9, merged: true },
      ]));
    });
    await waitFor(() => expect(screen.getByTestId('detections-note').textContent).toContain('2 detections'));
    expect(callBackendMock).toHaveBeenCalledWith('images:measure_detections', { path: '/stacks/M31.fits' }, expect.anything());
    expect(screen.getByTestId('detections-single').getAttribute('d')).not.toBe('');
    expect(screen.getByTestId('detections-merged').getAttribute('d')).not.toBe('');
  });

  it('draws dots and says to zoom in when the photometry circles are too small to see', async () => {
    callBackendMock.mockResolvedValue({
      ...makeDetections([{ x: 30, y: 40, flux: 5, merged: false }, { x: 60, y: 80, flux: 9, merged: true }]),
      aperture_radius_px: 0.5,
    });
    renderViewer(true);
    await waitFor(() => expect(screen.getByTestId('detections-note').textContent).toContain('Zoom in to see the photometry circles'));
    expect(screen.getByTestId('detections-layer').getAttribute('data-mode')).toBe('dots');
    expect(screen.getByTestId('detections-merged').getAttribute('d')).not.toBe('');
  });

  it('shows nothing when the backend refuses the image', async () => {
    callBackendMock.mockRejectedValue(new BackendError({ code: 'invalid_argument', message: 'The image must be inside the library folders.' }));
    renderViewer(true);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await waitFor(() => expect(callBackendMock).toHaveBeenCalled());
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('detections-layer')).toBeNull();
    expect(screen.queryByTestId('detections-note')).toBeNull();
  });

  it('shows a short message when the call fails for another reason', async () => {
    callBackendMock.mockRejectedValue(new Error('boom'));
    renderViewer(true);
    expect((await screen.findByText('The stars could not be found on this image.'))).toBeTruthy();
  });
});
