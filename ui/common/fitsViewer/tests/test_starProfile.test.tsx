/**
 * @file test_starProfile.test.tsx
 * @description Tests the click-to-measure star profile: a click always measures, a click on empty sky is
 * ignored, the panel's X only hides it, a click names the same pixel as the pixel readout, an image with no pixel values says so and sends nothing,
 * the panel shows the fits, the clipped badge and a failed fit's reason, the slider moves the cut live with no
 * call to the backend, the click sends a search radius that follows the zoom, and a profile that is not a star
 * shows its reason and no fitted curve. A view put back with `pickStar` opens the panel for the star at its center.
 * Also the Detections toggle and overlay.
 */
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RingHarmonic, StarProfileMeasurement } from '../../types/backendTypes';

/** A stand-in for the FITS worker: answers each pixel question the way the real one does for a bottom-up file. */
class FakeWorker extends EventTarget {
  /**
   * Answers a pixel question with the 1-based position and a fixed value.
   *
   * @param message The question.
   */
  postMessage(message: Record<string, unknown>): void {
    const column = message.column as number;
    const rowFromTop = message.rowFromTop as number;
    this.dispatchEvent(new MessageEvent('message', {
      data: {
        cmd: 'pixelComplete',
        requestId: message.requestId,
        imageId: message.imageId,
        result: { x: column + 1, y: 160 - rowFromTop, values: [1000] },
      },
    }));
  }
}

const fakeWorker = new FakeWorker();

/** What the stand-in loader reports; each test sets it before rendering. */
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

/** The traces last handed to each stand-in plot, by test id. */
interface StandInTrace {
  x: number[];
  y: number[];
  name?: string;
  hovertemplate?: string;
  hoverinfo?: string;
  marker?: { color?: string | string[] };
  type?: string;
  mode?: string;
  error_y?: { array: number[] };
  line?: { color?: string; dash?: string };
}
const plotData: Record<string, StandInTrace[]> = {};
/** The annotation (the FWHM label) last handed to the stand-in plot. */
let lastAnnotationFont: { size?: number; color?: string } | undefined;
/** The whole annotation last handed to the stand-in plot. */
let lastAnnotation: Record<string, unknown> | undefined;
vi.mock('../../components/plotting/BasePlot', () => ({
  BasePlot: (props: { data: StandInTrace[]; layout?: { xaxis?: { title?: { text: string } }; yaxis?: { title?: { text: string }; type?: string; range?: number[] } ; annotations?: Array<{ x: number; y: number; text?: string; xanchor?: string; xshift?: number; yanchor?: string; bgcolor?: string; font?: { size?: number; color?: string } }> }; 'data-testid'?: string }) => {
    const id = props['data-testid'] ?? 'plot';
    plotData[id] = props.data;
    lastAnnotationFont = props.layout?.annotations?.[0]?.font;
    lastAnnotation = props.layout?.annotations?.[0] as Record<string, unknown> | undefined;
    return (
      <div data-testid={id}>
        <span>{props.layout?.xaxis?.title?.text}</span>
        <span>{props.layout?.yaxis?.title?.text}</span>
        <span data-testid={`${id}-yaxis-type`}>{props.layout?.yaxis?.type}</span>
        <span data-testid={`${id}-yaxis-range`}>{JSON.stringify(props.layout?.yaxis?.range ?? null)}</span>
        <span data-testid={`${id}-layout`} hidden>{JSON.stringify(props.layout)}</span>
        <span data-testid={`${id}-annotation-y`}>{props.layout?.annotations?.[0]?.y}</span>
      </div>
    );
  },
}));

const callBackendMock = vi.fn();
/** What the stand-in backend answers to the inspector's own reads, which the tests below do not count. */
const detectionsAnswer = { path: '', width: 200, height: 200, detections: [], detection_count: 0, merged_count: 0, kernel_fwhm_px: 3, star_width_px: 3.2, aperture_radius_px: 4 };
vi.mock('../../services/backendApi', () => ({
  callBackend: (...args: unknown[]) => {
    if (args[0] === 'images:measure_detections') return Promise.resolve(detectionsAnswer);
    if (args[0] === 'astronomy:get') return Promise.resolve(null);
    return callBackendMock(...args);
  },
}));

import { FitsRenderer } from '../FitsRenderer';
import type { FitsRendererHandle } from '../FitsRenderer';
import { CUT_LENGTH_RANGE, StarProfilePanel, describeFollow, CUT_ANGLE_EXPLANATION, LOG_SCALE_TOOLTIP, PLOT_CAPTION, SET_DISPLAY_TO_LINEAR_MESSAGE } from '../StarProfilePanel';
import { DetectionsToggle, DETECTIONS_TOGGLE_TOOLTIP } from '../DetectionsToggle';

/**
 * Builds a cutout whose value at image pixel (x, y) is 10 x + y, so a bilinear read is exact anywhere.
 *
 * @param originX The 1-based image column of the first cutout column.
 * @param originY The 1-based image row of the first cutout row.
 * @param side Pixels along each side.
 * @param valueAt Replaces the ramp where it returns a value other than undefined.
 * @returns The rows of the cutout.
 */
function rampCutout(
  originX: number, originY: number, side: number,
  valueAt: (x: number, y: number) => number | null | undefined = () => undefined,
): (number | null)[][] {
  return Array.from({ length: side }, (_row, rowIndex) => Array.from({ length: side }, (_column, columnIndex) => {
    const x = originX + columnIndex;
    const y = originY + rowIndex;
    const custom = valueAt(x, y);
    return custom === undefined ? 10 * x + y : custom;
  }));
}

/**
 * Makes an answer from the backend.
 *
 * @param overrides Fields to change.
 * @returns The measurement.
 */
function makeMeasurement(overrides: Partial<StarProfileMeasurement> = {}): StarProfileMeasurement {
  return {
    path: '/stacks/M31.fits',
    color_note: null,
    clicked_x: 51,
    clicked_y: 120,
    center_x: 50.6,
    center_y: 119.8,
    radius_px: 10,
    radius_from_star_width: false,
    sky: 100,
    sky_noise: 3.5,
    sky_pixel_count: 300,
    pixel_scale_arcsec: 0.75,
    profile_radius_px: [0, 1, 2, 3],
    profile_values: [900, 500, 200, 50],
    peak: 900,
    peak_raw: 1000,
    image_maximum: 1000,
    clipped: false,
    clipped_reason: '',
    moffat: {
      model: 'moffat', fitted: true, fwhm_px: 3.2, fwhm_arcsec: 2.4, peak: 890, beta: 2.8, r_squared: 0.998,
      curve_radius_px: [0, 1, 2], curve_values: [890, 480, 190],
    },
    gaussian: {
      model: 'gaussian', fitted: true, fwhm_px: 3.0, fwhm_arcsec: 2.25, peak: 880, r_squared: 0.97,
      curve_radius_px: [0, 1, 2], curve_values: [880, 470, 180],
    },
    cut_angle_degrees: 0,
    cut_half_length_px: 10,
    cut_distance_px: [-2, -1, 0, 1, 2],
    cut_values: [190, 480, 900, 480, 190],
    cut_step_px: 0.5,
    // The star is near (51, 120); the cutout is 2 x 10 + 3 = 23 pixels square around that pixel.
    cutout: rampCutout(40, 109, 23),
    cutout_origin_x: 40,
    cutout_origin_y: 109,
    snapped: false,
    snap_distance_px: 0,
    star_found: true,
    not_a_star_reason: null,
    // The ring: 6 to 10 px, four direction bins, one significant one-sided term toward 180 degrees, no neighbors.
    ring_inner_px: 6,
    ring_outer_px: 10,
    ring_sky: 100,
    ring_bin_angle_degrees: [2.5, 92.5, 182.5, 272.5],
    ring_bin_values: [10, 12, 20, 12],
    ring_bin_counts: [30, 30, 30, 30],
    ring_harmonics: ringTerms(),
    ring_constant: 1,
    ring_scale_value: 14,
    ring_neighbors: [],
    ring_masked_neighbor_count: 0,
    ring_masked_fraction: 0,
    ring_note: null,
    peak_over_sky_noise: 300,
    edge_distance_px: 100,
    coverage_fraction: 1,
    aperture_radius_px: 4,
    sky_inner_px: 7,
    sky_outer_px: 12,
    circle_neighbor_count: 0,
    sky_ring_neighbor_count: 0,
    star_elongation: 1.08,
    star_elongation_angle_deg: 20,
    star_shape_judgeable: true,
    star_shape_note: null,
    star_shape_detail: null,
    trailing_elongation_limit: 1.32,
    ...overrides,
  };
}

/**
 * Makes the eight Fourier terms of a ring: a significant one-sided term toward 180 degrees, a significant
 * oval term, and six terms that are only noise.
 *
 * @returns The terms, orders 1 to 8.
 */
function ringTerms(): RingHarmonic[] {
  return Array.from({ length: 8 }, (_unused, index) => {
    const order = index + 1;
    if (order === 1) return { order, amplitude: 0.13, amplitude_error: 0.02, amplitude_debiased: 0.124, significance: 6.5, significant: true, direction_degrees: 180 };
    if (order === 2) return { order, amplitude: 0.035, amplitude_error: 0.01, amplitude_debiased: 0.033, significance: 3.5, significant: true, direction_degrees: 10 };
    return { order, amplitude: 0.01, amplitude_error: 0.01, amplitude_debiased: 0, significance: 1, significant: false, direction_degrees: 20 };
  });
}

/**
 * Renders the viewer around a 200 by 160 FITS image.
 *
 * @param showDetections True to turn the Detections overlay on.
 * @returns The element that takes the pointer.
 */
function renderViewer(showDetections = false): HTMLElement {
  const resolveMeasurePath = async () => '/stacks/M31.fits';
  const { container } = render(
    <FitsRenderer
      imageUrl="/static/M31.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="M 31" autoPanTrigger={0} showDetections={showDetections} resolveMeasurePath={resolveMeasurePath}
    />
  );
  return container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
}

/**
 * Presses and releases the pointer at one spot, as a click.
 *
 * @param element The element that takes the pointer.
 * @param x Pointer x in screen pixels.
 * @param y Pointer y in screen pixels.
 * @param releaseX Where the pointer is when released (default: the press spot).
 */
function click(element: HTMLElement, x: number, y: number, releaseX = x): void {
  act(() => {
    element.dispatchEvent(new MouseEvent('pointerdown', { clientX: x, clientY: y, bubbles: true }));
    element.dispatchEvent(new MouseEvent('pointerup', { clientX: releaseX, clientY: y, bubbles: true }));
  });
}

describe('star profile', () => {
  beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockResolvedValue(makeMeasurement());
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/M31.fits';
    // jsdom has no pointer capture; the pan code asks for it on every press.
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('measures the clicked pixel, the same one the readout names, ', async () => {
    const element = renderViewer();
    act(() => {
      element.dispatchEvent(new MouseEvent('pointermove', { clientX: 50, clientY: 40, bubbles: true }));
    });
    const positionBadge = screen.getByTestId('pixel-readout').querySelector('[title*="starts at 1"]');
    const [, readoutX, readoutY] = /x (\d+)\s+y (\d+)/.exec(positionBadge?.textContent ?? '') ?? [];
    expect(readoutX).toBeDefined();
    click(element, 50, 40);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(1));
    expect(callBackendMock).toHaveBeenCalledWith(
      'images:measure_star_profile',
      expect.objectContaining({ path: '/stacks/M31.fits', x: Number(readoutX), y: Number(readoutY), cut_angle_degrees: 0 }),
      expect.anything(),
    );
    expect(await screen.findByTestId('star-profile-width')).toBeTruthy();
    expect(screen.getByTestId('star-profile-marker')).toBeTruthy();
  });

  it('draws a ring one FWHM across, in the same scale as the cut line, with a tooltip', async () => {
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    const marker = screen.getByTestId('star-profile-marker');
    const ring = marker.querySelector('circle') as SVGCircleElement;
    const cut = marker.querySelector('line') as SVGLineElement;
    // The cut runs straight along a column (angle 0), 2 x 10 pixels long; the FWHM is 3.2 pixels.
    const screenPixelsPerFilePixel = Math.abs(Number(cut.getAttribute('y2')) - Number(cut.getAttribute('y1'))) / 20;
    expect(screenPixelsPerFilePixel).toBeGreaterThan(0);
    expect(Number(ring.getAttribute('r')) * 2).toBeCloseTo(3.2 * screenPixelsPerFilePixel, 5);
    expect(ring.querySelector('title')?.textContent).toBe('Circle one FWHM across');
  });

  it('keeps the true size of a tiny ring and thickens its line', async () => {
    callBackendMock.mockResolvedValue(makeMeasurement({
      moffat: { model: 'moffat', fitted: true, fwhm_px: 0.2, fwhm_arcsec: 0.15, beta: 2.8, r_squared: 0.99, curve_radius_px: [0, 1], curve_values: [10, 5] },
    }));
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    const ring = screen.getByTestId('star-profile-marker').querySelector('circle') as SVGCircleElement;
    expect(Number(ring.getAttribute('r'))).toBeLessThan(4);
    expect(ring.getAttribute('class')).toContain('fits-renderer__profile-ring--small');
  });

  it('does not measure when the pointer moved between press and release (a pan)', async () => {
    const element = renderViewer();
    click(element, 50, 40, 90);
    await act(async () => { await Promise.resolve(); });
    expect(callBackendMock).not.toHaveBeenCalled();
  });

  it('says to set Display to Linear, and sends nothing, when the picture holds no pixel values', async () => {
    loaderState.parsedData = null;
    loaderState.bitmap = { width: 100, height: 80 };
    const element = renderViewer();
    click(element, 50, 40);
    expect(await screen.findByTestId('star-profile-message')).toHaveProperty('textContent', SET_DISPLAY_TO_LINEAR_MESSAGE);
    expect(SET_DISPLAY_TO_LINEAR_MESSAGE).toBe('Set Display to Linear to measure stars here.');
    expect(callBackendMock).not.toHaveBeenCalled();
  });

  it('shows a failed request as a message in the panel', async () => {
    callBackendMock.mockRejectedValue(new Error('That file is outside the library.'));
    const element = renderViewer();
    click(element, 50, 40);
    expect((await screen.findByTestId('star-profile-error')).textContent).toBe('That file is outside the library.');
  });

  it('moves the cut on the image at once when the slider moves, with no call to the backend', async () => {
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    const cut = () => screen.getByTestId('star-profile-marker').querySelector('line') as SVGLineElement;
    // At 0 degrees the cut runs along a column: no horizontal reach.
    expect(Number(cut().getAttribute('x2'))).toBeCloseTo(Number(cut().getAttribute('x1')), 6);
    fireEvent.change(screen.getByLabelText('Cut angle in degrees'), { target: { value: '90' } });
    expect(Math.abs(Number(cut().getAttribute('x2')) - Number(cut().getAttribute('x1')))).toBeGreaterThan(1);
    expect(Number(cut().getAttribute('y2'))).toBeCloseTo(Number(cut().getAttribute('y1')), 6);
    // The plotted points follow too: along a row, the ramp rises 10 per pixel.
    const points = plotData['star-profile-plot'][0];
    expect(points.y[points.y.length - 1] - points.y[0]).toBeCloseTo(10 * (points.x[points.x.length - 1] - points.x[0]), 6);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 500)); });
    expect(callBackendMock).toHaveBeenCalledTimes(1);
  });

  it('sends a search radius of 6 screen pixels in image pixels, within 3 and 50, at two zoom levels', async () => {
    const handle = React.createRef<FitsRendererHandle>();
    const { container } = render(
      <FitsRenderer
        ref={handle}
        imageUrl="/static/M31.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 31" autoPanTrigger={0} resolveMeasurePath={async () => '/stacks/M31.fits'}
      />
    );
    const element = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    act(() => handle.current?.zoomToScale(1));
    click(element, 10, 10);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(1));
    expect(callBackendMock.mock.calls[0][1].search_radius_px).toBeCloseTo(6, 6);
    act(() => handle.current?.zoomToScale(0.25));
    click(element, 10, 10);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(2));
    expect(callBackendMock.mock.calls[1][1].search_radius_px).toBeCloseTo(24, 6);
    act(() => handle.current?.zoomToScale(8));
    click(element, 10, 10);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(3));
    expect(callBackendMock.mock.calls[2][1].search_radius_px).toBe(3);
  });

  const emptySky = () => makeMeasurement({ star_found: false, not_a_star_reason: 'Too flat to be a star.' });

  it('ignores a click on empty sky when the panel is closed: no panel, no marker, no pin', async () => {
    callBackendMock.mockResolvedValue(emptySky());
    const element = renderViewer();
    click(element, 50, 40);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(1));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('star-profile-panel')).toBeNull();
    expect(screen.queryByTestId('star-profile-marker')).toBeNull();
  });

  it('keeps the old star and its pin when a click on empty sky comes while the panel is open', async () => {
    const element = renderViewer();
    click(element, 50, 40);
    expect((await screen.findByTestId('star-profile-width')).textContent).toContain('3.2');
    callBackendMock.mockResolvedValue(emptySky());
    click(element, 120, 90);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(2));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByTestId('star-profile-width').textContent).toContain('3.2');
    expect(screen.queryByTestId('star-profile-measuring')).toBeNull();
    // The pin is still the first star: a longer cut measures the old anchor again.
    const marker = screen.getByTestId('star-profile-marker').querySelector('circle') as SVGCircleElement;
    expect(marker.getAttribute('class')).not.toContain('--neutral');
  });

  it('hides the panel with its X, and a click on a star opens it again with the new star', async () => {
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    fireEvent.click(screen.getByRole('button', { name: 'Close star profile' }));
    expect(screen.queryByTestId('star-profile-panel')).toBeNull();
    callBackendMock.mockResolvedValue(makeMeasurement({ center_x: 80.2, center_y: 70.4 }));
    click(element, 90, 60);
    expect(await screen.findByTestId('star-profile-panel')).toBeTruthy();
    expect(callBackendMock).toHaveBeenCalledTimes(2);
    expect(callBackendMock.mock.calls[1][1]).toMatchObject({ search_radius_px: expect.any(Number) });
    await screen.findByTestId('star-profile-width');
  });
});

/** A measurement with the given half-length, whose cutout reaches that far from the center, on the ramp. */
function longMeasurement(length: number): StarProfileMeasurement {
  return makeMeasurement({
    cut_half_length_px: length,
    cutout: rampCutout(51 - (Math.ceil(length) + 1), 120 - (Math.ceil(length) + 1), 2 * Math.ceil(length) + 3),
    cutout_origin_x: 51 - (Math.ceil(length) + 1),
    cutout_origin_y: 120 - (Math.ceil(length) + 1),
    center_x: 51,
    center_y: 120,
  });
}

describe('a star picked by a requested view', () => {
  beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockResolvedValue(makeMeasurement());
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/M31.fits';
    // The view is put back only in a container with a size.
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 400 });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 320 });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  });

  /** "Back to image" asks for the star at the new center; the panel opens for it as after a click there. */
  it('opens the star profile for the star at the center, once', async () => {
    const restoreView = {
      key: 'star-focus-1', view: { x: 0.25, y: 0.25, zoomOverFit: 1, turn: 0 }, scale: 3, pictureWidth: 200, pickStar: true,
    };
    const { rerender } = render(
      <FitsRenderer
        imageUrl="/static/M31.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 31" autoPanTrigger={0} resolveMeasurePath={async () => '/stacks/M31.fits'} restoreView={restoreView}
      />
    );
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(1));
    // Column 50 and row 40 from the top of the 200 by 160 picture: file pixel (51, 120). 6 screen pixels at 3x is 2, so 3.
    expect(callBackendMock).toHaveBeenCalledWith(
      'images:measure_star_profile',
      expect.objectContaining({ path: '/stacks/M31.fits', x: 51, y: 120, search_radius_px: 3 }),
      expect.anything(),
    );
    expect(await screen.findByTestId('star-profile-width')).toBeTruthy();
    expect(screen.getByTestId('star-profile-marker')).toBeTruthy();
    rerender(
      <FitsRenderer
        imageUrl="/static/M31.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 31" autoPanTrigger={0} resolveMeasurePath={async () => '/stacks/M31.fits'} restoreView={{ ...restoreView }}
      />
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(callBackendMock).toHaveBeenCalledTimes(1);
  });
});

describe('cut length', () => {
  beforeEach(() => {
    callBackendMock.mockReset();
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/M31.fits';
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('rebuilds the cut locally within the cutout and measures again once, after a rest, when it grows past it', async () => {
    callBackendMock.mockResolvedValueOnce(longMeasurement(30)).mockResolvedValue(longMeasurement(60));
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    const slider = screen.getByLabelText('Cut length in pixels') as HTMLInputElement;
    expect(slider.min).toBe(String(CUT_LENGTH_RANGE.min));
    expect(slider.max).toBe(String(CUT_LENGTH_RANGE.max));
    expect(slider.value).toBe('30');
    // Shorter than the cutout: only the plot changes, and no call is made, even after a long wait.
    fireEvent.change(slider, { target: { value: '25' } });
    expect(plotData['star-profile-plot'][0].x).toHaveLength(2 * 50 + 1);
    expect(plotData['star-profile-plot'][0].x[0]).toBe(-25);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 500)); });
    expect(callBackendMock).toHaveBeenCalledTimes(1);
    // Longer than the cutout: several quick moves make one call, after the slider rests.
    fireEvent.change(slider, { target: { value: '40' } });
    fireEvent.change(slider, { target: { value: '50' } });
    fireEvent.change(slider, { target: { value: '60' } });
    expect(callBackendMock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(callBackendMock.mock.calls[1][1]).toMatchObject({ x: 51, y: 120, cut_half_length_px: 60, search_radius_px: expect.any(Number) });
    await waitFor(() => expect(plotData['star-profile-plot'][0].x[0]).toBe(-60));
  });
});

describe('describeFollow', () => {
  it('words each method for the line under the header', () => {
    expect(describeFollow('sky')).toBe('Followed by sky position');
    expect(describeFollow('stack alignment')).toBe('Followed by stack alignment');
    expect(describeFollow('stack alignment + sky')).toBe('Followed by stack alignment and sky position');
    expect(describeFollow('same pixel')).toBe('Same pixel (guess)');
  });
});

describe('pinned star followed to another image', () => {
  let currentPath = '/stacks/A.fits';
  let mapResult: { x: number | null; y: number | null; method: string; note: string } = {
    x: 80, y: 90, method: 'sky', note: 'Followed by the plate solution of both images.',
  };

  /** Answers each RPC by name, the way the backend does. */
  const answer = (action: string) => {
    if (action === 'images:map_position') return Promise.resolve(mapResult);
    return Promise.resolve(makeMeasurement());
  };

  beforeEach(() => {
    currentPath = '/stacks/A.fits';
    mapResult = { x: 80, y: 90, method: 'sky', note: 'Followed by the plate solution of both images.' };
    callBackendMock.mockReset();
    callBackendMock.mockImplementation(answer);
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/A.fits';
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** The viewer on one image; `resolveMeasurePath` follows `currentPath`. */
  const viewerOn = (name: string) => (
    <FitsRenderer
      imageUrl={`/static/${name}.fits`} imageBlob={null} frameInfo={null} loading={false} error={null}
      selectedTarget="M 31" autoPanTrigger={0} resolveMeasurePath={async () => currentPath}
    />
  );

  /**
   * Shows another image: the loader reports it and the viewer is given its address.
   *
   * @param rerender The render result's rerender.
   * @param name The image's short name.
   */
  const switchTo = (rerender: (ui: React.ReactElement) => void, name: string) => {
    currentPath = `/stacks/${name}.fits`;
    loaderState.parsedData = { imageId: 8 + name.charCodeAt(0), w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.loadedKey = `/static/${name}.fits`;
    rerender(viewerOn(name));
  };

  /** The measurement calls so far. */
  const measureCalls = () => callBackendMock.mock.calls.filter((call) => call[0] === 'images:measure_star_profile');
  const mapCalls = () => callBackendMock.mock.calls.filter((call) => call[0] === 'images:map_position');

  it('maps the pinned star to the new image and measures it there, keeping the panel settings', async () => {
    const { container, rerender } = render(viewerOn('A'));
    click(container.querySelector('.fits-renderer__canvas-container') as HTMLElement, 10, 10);
    await screen.findByTestId('star-profile-width');
    // Settings the user made: the tab, the cut angle, and the log scale.
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    fireEvent.click(screen.getByRole('button', { name: 'Log scale' }));
    fireEvent.change(screen.getByLabelText('Cut angle in degrees'), { target: { value: '30' } });
    expect(measureCalls()).toHaveLength(1);
    switchTo(rerender, 'B');
    await waitFor(() => expect(measureCalls()).toHaveLength(2));
    // The pin is the refined center (50.6, 119.8) of the star measured on image A.
    expect(mapCalls()).toHaveLength(1);
    expect(mapCalls()[0][1]).toEqual({ from_path: '/stacks/A.fits', x: 50.6, y: 119.8, to_path: '/stacks/B.fits' });
    expect(measureCalls()[1][1]).toMatchObject({ path: '/stacks/B.fits', x: 80, y: 90, cut_angle_degrees: 30 });
    expect((await screen.findByTestId('star-profile-follow')).textContent).toBe('Followed by sky position');
    expect(screen.getByRole('button', { name: 'Hide optics detail' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: 'Log scale' }).getAttribute('aria-pressed')).toBe('true');
    expect((screen.getByLabelText('Cut angle in degrees') as HTMLInputElement).value).toBe('30');
  });

  it('says "Same pixel (guess)" and searches at least 10 image pixels around the guess', async () => {
    mapResult = { x: 50.6, y: 119.8, method: 'same pixel', note: 'No plate solution or registration links these images; this is a guess.' };
    const { container, rerender } = render(viewerOn('A'));
    click(container.querySelector('.fits-renderer__canvas-container') as HTMLElement, 10, 10);
    await screen.findByTestId('star-profile-width');
    switchTo(rerender, 'B');
    await waitFor(() => expect(measureCalls()).toHaveLength(2));
    expect(measureCalls()[1][1].search_radius_px).toBe(10);
    expect((await screen.findByTestId('star-profile-follow')).textContent).toBe('Same pixel (guess)');
  });

  it('shows the reason when the star cannot be followed, and finds it again when switching back', async () => {
    const { container, rerender } = render(viewerOn('A'));
    click(container.querySelector('.fits-renderer__canvas-container') as HTMLElement, 10, 10);
    await screen.findByTestId('star-profile-width');
    mapResult = { x: null, y: null, method: 'none', note: 'Siril could not register that frame, so it is not in the stack.' };
    switchTo(rerender, 'B');
    expect((await screen.findByTestId('star-profile-message')).textContent)
      .toBe('The star could not be followed to this image: Siril could not register that frame, so it is not in the stack.');
    expect(measureCalls()).toHaveLength(1);
    // Back on the first image, the pinned star is measured again where it was pinned.
    switchTo(rerender, 'A');
    await waitFor(() => expect(measureCalls()).toHaveLength(2));
    expect(measureCalls()[1][1]).toMatchObject({ path: '/stacks/A.fits', x: 50.6, y: 119.8 });
    expect(mapCalls()).toHaveLength(1);
  });

  it('keeps the old pin when the new spot is not a star, and a click on another star re-pins', async () => {
    const { container, rerender } = render(viewerOn('A'));
    const element = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    click(element, 10, 10);
    await screen.findByTestId('star-profile-width');
    callBackendMock.mockImplementation((action: string) => (
      action === 'images:map_position'
        ? Promise.resolve(mapResult)
        : Promise.resolve(makeMeasurement({ star_found: false, not_a_star_reason: 'Too flat to be a star.' }))
    ));
    switchTo(rerender, 'B');
    expect((await screen.findByTestId('star-profile-width')).textContent).toBe('Too flat to be a star.');
    // A third image is followed from the first image's pin, not from the empty spot on B.
    switchTo(rerender, 'C');
    await waitFor(() => expect(mapCalls()).toHaveLength(2));
    expect(mapCalls()[1][1]).toMatchObject({ from_path: '/stacks/A.fits', x: 50.6, y: 119.8, to_path: '/stacks/C.fits' });
    // A click on a star of image C pins that star: the next switch maps from it.
    callBackendMock.mockImplementation(answer);
    click(element, 20, 20);
    await waitFor(() => expect(measureCalls().some((call) => call[1].path === '/stacks/C.fits' && call[1].search_radius_px !== undefined)).toBe(true));
    await screen.findByTestId('star-profile-width');
    switchTo(rerender, 'A');
    await waitFor(() => expect(mapCalls()).toHaveLength(3));
    expect(mapCalls()[2][1]).toMatchObject({ from_path: '/stacks/C.fits' });
  });
});

describe('around the star view', () => {
  const anchorTop = { x: 51, y: 120, firstRowAtTop: true, searchRadiusPx: 6 };
  const noop = () => undefined;

  it('shows the second tab with its plot, screen directions, and the two angle markers', () => {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor: anchorTop, measurement: makeMeasurement() }} cutAngleDegrees={30} onCutAngleChange={noop} onClose={noop} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    expect(screen.getByTestId('star-profile-ring-plot')).toBeTruthy();
    expect(screen.queryByTestId('star-profile-plot')).toBeNull();
    const layout = ringLayout();
    // The first row is at the top, so 0 degrees (up a column of the file) points down the screen.
    expect(layout.xaxis.ticktext).toEqual(['Down<br>0°', 'Right<br>90°', 'Up<br>180°', 'Left<br>270°', 'Down<br>360°']);
    expect(layout.xaxis.range).toEqual([0, 360]);
    expect(layout.shapes.map((shape) => shape.x0)).toEqual([30, 210]);
  });

  it('names the screen directions the other way round for a bottom-up picture', () => {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor: { ...anchorTop, firstRowAtTop: false }, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    expect(ringLayout().xaxis.ticktext.slice(0, 4)).toEqual(['Up<br>0°', 'Right<br>90°', 'Down<br>180°', 'Left<br>270°']);
  });

  it('draws the two ring circles on the image instead of the cut line, at the default radii', async () => {
    callBackendMock.mockResolvedValue(longMeasurement(20));
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/M31.fits';
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    const marker = screen.getByTestId('star-profile-marker');
    expect(marker.querySelector('line')).not.toBeNull();
    expect(marker.querySelector('[data-testid="star-profile-band-inner"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    const inner = screen.getByTestId('star-profile-band-inner');
    const outer = screen.getByTestId('star-profile-band-outer');
    expect(marker.querySelector('line')).toBeNull();
    // The automatic ring for this Moffat star (FWHM 3.2, beta 2.8, peak 890, noise 3.5): about 6 and 10 pixels, at the same scale as the FWHM ring.
    const ringRadius = Number(marker.querySelector('.fits-renderer__profile-ring')?.getAttribute('r'));
    const screenPixelsPerFilePixel = (ringRadius * 2) / 3.2;
    expect(Number(inner.getAttribute('r'))).toBeCloseTo(6 * screenPixelsPerFilePixel, 5);
    expect(Number(outer.getAttribute('r'))).toBeCloseTo(10 * screenPixelsPerFilePixel, 5);
    vi.unstubAllGlobals();
  });
});

describe('the ring is always automatic', () => {
  const anchor = { x: 51, y: 120, firstRowAtTop: true, searchRadiusPx: 6 };
  const noop = () => undefined;

  /**
   * Renders the panel on the ring tab.
   *
   * @param measurement The star.
   * @returns The render result, to rerender with another star.
   */
  function renderRing(measurement: StarProfileMeasurement) {
    const result = render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    return result;
  }

  it('has no ring sliders and no Auto button, only the radii as plain text', () => {
    renderRing(longMeasurement(30));
    expect(screen.queryByLabelText('Inner radius of the ring in pixels')).toBeNull();
    expect(screen.queryByLabelText('Outer radius of the ring in pixels')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Auto' })).toBeNull();
    expect(screen.getByText('Ring 6 to 10 px')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'About the ring view' })).toBeNull();
    // The cut angle slider stays.
    expect(screen.getByLabelText('Cut angle in degrees')).toBeTruthy();
  });

  it('takes the ring radii from each new measurement', () => {
    const { rerender } = renderRing(longMeasurement(30));
    const wider = longMeasurement(30);
    wider.ring_inner_px = 12.4;
    wider.ring_outer_px = 31.6;
    rerender(
      <StarProfilePanel state={{ status: 'ready', anchor: { ...anchor }, measurement: wider }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(screen.getByText('Ring 12 to 32 px')).toBeTruthy();
  });

  it('notes that few pixels fall in each direction when the ring is close in', () => {
    renderRing(makeMeasurement({ ring_bin_counts: [30, 2, 30, 30] }));
    expect(screen.getByTestId('star-profile-ring-note').textContent).toBe('Few pixels per direction this close in');
  });
});

describe('the ring analysis from the library', () => {
  const anchor = { x: 51, y: 120, firstRowAtTop: false, searchRadiusPx: 6 };
  const noop = () => undefined;

  /**
   * Renders the panel on the ring tab.
   *
   * @param measurement The star.
   * @param firstRowAtTop True for a picture with the file's first row at the top.
   */
  function renderRing(measurement: StarProfileMeasurement, firstRowAtTop = false): void {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor: { ...anchor, firstRowAtTop }, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
  }

  it('plots the library bins and a smooth curve of the constant plus the significant terms', () => {
    renderRing(makeMeasurement());
    const traces = plotData['star-profile-ring-plot'];
    expect(traces.map((trace) => trace.name)).toEqual(['around the star', 'significant terms']);
    expect(traces[0].x).toEqual([2.5, 92.5, 182.5, 272.5]);
    expect(traces[0].y).toEqual([10, 12, 20, 12]);
    const curve = traces[1];
    expect(curve.mode).toBe('lines');
    // At 180 degrees the one-sided term is at its peak: 14 * 1 * (1 + 0.13 + 0.035 cos(2 (180 - 10))).
    const at180 = curve.y[curve.x.indexOf(180)];
    expect(at180).toBeCloseTo(14 * (1 + 0.13 + 0.035 * Math.cos((2 * 170 * Math.PI) / 180)), 6);
    // At 0 degrees it is at its trough: the one-sided term subtracts.
    expect(curve.y[0]).toBeLessThan(14);
  });

  it('draws a flat line when no term is significant', () => {
    const quiet = ringTerms().map((term) => ({ ...term, significant: false }));
    renderRing(makeMeasurement({ ring_harmonics: quiet }));
    const curve = plotData['star-profile-ring-plot'][1];
    expect(new Set(curve.y).size).toBe(1);
    expect(curve.y[0]).toBeCloseTo(14, 6);
    expect(screen.getByTestId('star-profile-ring-summary').textContent).toBe('Round within the noise');
  });

  it('draws no curve when the ring was not analyzed, and says why', () => {
    renderRing(makeMeasurement({
      ring_harmonics: [], ring_constant: null, ring_scale_value: null, ring_note: 'The ring is not analyzed because no star was found.',
    }));
    expect(plotData['star-profile-ring-plot'].map((trace) => trace.name)).toEqual(['around the star']);
    expect(screen.queryByTestId('star-profile-harmonics-plot')).toBeNull();
    expect(screen.getByTestId('star-profile-ring-summary').textContent).toBe('The ring is not analyzed because no star was found.');
  });

  it('shows a bar for each order with a 1 sigma error bar, significant bars in the accent color', () => {
    renderRing(makeMeasurement());
    const bars = plotData['star-profile-harmonics-plot'][0];
    expect(bars.type).toBe('bar');
    expect(bars.x).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(bars.y[0]).toBeCloseTo(12.4, 6);
    expect(bars.error_y?.array[0]).toBeCloseTo(2, 6);
    const colors = bars.marker?.color as string[];
    expect(colors[0]).toBe(colors[1]);
    expect(colors[0]).not.toBe(colors[2]);
    expect(colors.slice(2)).toEqual(new Array(6).fill(colors[2]));
  });

  it('summarizes the strongest significant terms, naming the screen direction of the one-sided term', () => {
    // 180 degrees points down the screen for a picture with the first row at the top, up otherwise.
    renderRing(makeMeasurement(), true);
    expect(screen.getByTestId('star-profile-ring-summary').textContent).toBe('One-sided 12% ± 2.0% toward Up; oval 3.3% ± 1.0%');
  });

  it('says Down for the same star in a bottom-up picture', () => {
    renderRing(makeMeasurement(), false);
    expect(screen.getByTestId('star-profile-ring-summary').textContent).toContain('toward Down');
  });

  it('shows the ring radii and the number of neighbors left out as plain text', () => {
    renderRing(makeMeasurement({
      ring_inner_px: 4.2, ring_outer_px: 24.4, ring_masked_neighbor_count: 2,
      ring_neighbors: [{ x: 60, y: 130, mask_radius_px: 4, pixels_masked: 20 }, { x: 40, y: 110, mask_radius_px: 3, pixels_masked: 12 }],
    }));
    expect(screen.getByText('Ring 4 to 24 px')).toBeTruthy();
    expect(screen.getByText('2 neighbors left out')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /neighbor/i })).toBeNull();
  });

  it('uses the singular for one neighbor and says so when there are none', () => {
    renderRing(makeMeasurement({ ring_masked_neighbor_count: 1 }));
    expect(screen.getByText('1 neighbor left out')).toBeTruthy();
    cleanup();
    renderRing(makeMeasurement());
    expect(screen.getByText('No neighbors left out')).toBeTruthy();
  });

  it('draws the masked neighbors as dashed circles in the marker layer on the ring tab only', async () => {
    callBackendMock.mockResolvedValue(makeMeasurement({
      center_x: 51, center_y: 120, ring_masked_neighbor_count: 1,
      ring_neighbors: [{ x: 61, y: 120, mask_radius_px: 4, pixels_masked: 20 }],
    }));
    loaderState.parsedData = { imageId: 7, w: 200, h: 160, channels: 1, min: 0, max: 65535 };
    loaderState.bitmap = null;
    loaderState.loadedKey = '/static/M31.fits';
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    const element = renderViewer();
    click(element, 50, 40);
    await screen.findByTestId('star-profile-width');
    const marker = screen.getByTestId('star-profile-marker');
    expect(screen.queryByTestId('star-profile-neighbor')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    const circle = await screen.findByTestId('star-profile-neighbor');
    expect(marker.contains(circle)).toBe(true);
    expect(circle.getAttribute('class')).toContain('fits-renderer__profile-neighbor');
    // The mask radius is 4 file pixels, in the scale of the ring's outer circle (10 file pixels).
    const outer = Number(screen.getByTestId('star-profile-band-outer').getAttribute('r'));
    expect(Number(circle.getAttribute('r'))).toBeCloseTo(outer * 0.4, 5);
    // Ten file pixels to the right of the star's center, in the same scale.
    const center = marker.querySelector('.fits-renderer__profile-ring') as SVGCircleElement;
    expect(Number(circle.getAttribute('cx')) - Number(center.getAttribute('cx'))).toBeCloseTo(outer, 5);
    expect(circle.getAttribute('cy')).toBe(center.getAttribute('cy'));
    vi.unstubAllGlobals();
  });
});

/**
 * Reads the layout last handed to the ring plot stand-in.
 *
 * @returns The ticks, range and shapes.
 */
function ringLayout(): { xaxis: { ticktext: string[]; range: number[] }; shapes: Array<{ x0: number }> } {
  return JSON.parse(screen.getByTestId('star-profile-ring-plot-layout').textContent ?? '{}');
}

describe('StarProfilePanel', () => {
  beforeEach(() => {
    // The theme's tokens, so the colors the panel reads can be told apart.
    const root = document.documentElement.style;
    root.setProperty('--plot-accent', 'rgb(53, 132, 228)');
    root.setProperty('--text-dim', 'rgb(170, 170, 170)');
    root.setProperty('--plot-reference-line', 'rgb(200, 200, 100)');
    root.setProperty('--small-font-size', '11px');
  });
  afterEach(() => {
    for (const token of ['--plot-accent', '--text-dim', '--plot-reference-line', '--small-font-size']) {
      document.documentElement.style.removeProperty(token);
    }
  });

  const anchor = { x: 51, y: 120, firstRowAtTop: false, searchRadiusPx: 6 };
  const noop = () => undefined;

  it('shows only the width, one plot with a mirrored curve and a half-height line, and the angle slider', () => {
    const { container } = render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(screen.getByTestId('star-profile-width').textContent).toBe('FWHM 3.20 px (2.40″)');
    // The line plot is the main view; the ring view is behind a collapsed "Optics detail" link.
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Optics detail' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('star-profile-ring-plot')).toBeNull();
    for (const removed of [/beta/, /r²/, /sky \d/, /peak \d/, /Gaussian/, /Moffat/, /center/]) {
      expect(container.textContent).not.toMatch(removed);
    }
    expect(screen.queryByText('Clipped')).toBeNull();
    expect(screen.getByText('distance through the star (px)')).toBeTruthy();
    expect(screen.getByText('above sky')).toBeTruthy();
    const traces = plotData['star-profile-plot'];
    expect(traces.map((trace) => trace.name)).toEqual(['pixels along the cut', 'fitted shape', 'FWHM']);
    // 2 x 20 + 1 samples, half a pixel apart, from -10 to +10; along a column the ramp rises 1 per pixel.
    expect(traces[0].x).toHaveLength(41);
    expect(traces[0].x[0]).toBe(-10);
    expect(traces[0].x[40]).toBe(10);
    expect(traces[0].y[20]).toBeCloseTo(10 * 50.6 + 119.8, 6);
    expect(traces[0].y[40] - traces[0].y[0]).toBeCloseTo(20, 6);
    expect(traces[1].x).toEqual([-2, -1, 0, 0, 1, 2]);
    expect(traces[1].y).toEqual([190, 480, 890, 890, 480, 190]);
    // Half of the fitted peak (890), between the two half-height points at plus and minus half the FWHM.
    expect(traces[2].x).toEqual([-1.6, 1.6]);
    expect(traces[2].y).toEqual([445, 445]);
    expect(screen.getByText(PLOT_CAPTION)).toBeTruthy();
    expect(PLOT_CAPTION).toBe('Along the dashed line');
    fireEvent.focus(screen.getByRole('button', { name: 'About the cut angle' }));
    expect(screen.getByRole('note').textContent).toBe(CUT_ANGLE_EXPLANATION);
  });

  it('puts the FWHM label to the right of the right end of the half-max line, with the header value', () => {
    // The plot reaches 10 pixels each way and the right end is at 1.6, so the label goes on the right.
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(lastAnnotation).toMatchObject({
      x: 1.6, y: 445, text: 'FWHM 3.20 px', xanchor: 'left', yanchor: 'middle', xshift: 6, borderwidth: 0,
    });
    expect(lastAnnotation?.bgcolor).toBeTruthy();
    expect(screen.getByTestId('star-profile-width').textContent).toContain('FWHM 3.20 px');
  });

  it('moves the FWHM label to the left end when the right end is in the right quarter of the plot', () => {
    // A cut half-length of 3 and a FWHM of 3.2: the right end (1.6) is beyond half of the range.
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement({ cut_half_length_px: 3 }) }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(lastAnnotation).toMatchObject({ x: -1.6, xanchor: 'right', xshift: -6 });
  });

  it('draws the curve in the accent blue, the points in light neutral, and a visible dashed FWHM line with a themed label', () => {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    const [points, curve, fwhmLine] = plotData['star-profile-plot'];
    expect(curve.line?.color).toBe('rgb(53, 132, 228)');
    expect(points.marker?.color).toBe('rgb(170, 170, 170)');
    expect(fwhmLine.line).toMatchObject({ color: 'rgb(200, 200, 100)', dash: 'dash' });
    expect(lastAnnotationFont).toEqual({ size: 11, color: 'rgb(200, 200, 100)' });
  });

  it('writes rounded values with units in the hover labels and drops the trace-name box', () => {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    const [points, curve, fwhmLine] = plotData['star-profile-plot'];
    expect(points.hovertemplate).toBe('x %{x:.2f} px, %{y:.3g}<extra></extra>');
    expect(curve.hovertemplate).toBe('fitted shape: %{y:.3g} at %{x:.2f} px<extra></extra>');
    expect(fwhmLine.hoverinfo).toBe('skip');
  });

  it('closes from an icon button named by its aria-label', () => {
    const onClose = vi.fn();
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={onClose} />
    );
    const close = screen.getByRole('button', { name: 'Close star profile' });
    expect(close.getAttribute('title')).toBe('Close star profile');
    expect(close.textContent).toBe('');
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('switches the brightness axis to log and raises non-positive values to the sky noise', () => {
    const measurement = makeMeasurement({
      sky_noise: 3.5,
      // Three samples straight along the column x = 5, at y = 4, 5 and 6.
      center_x: 5,
      center_y: 5,
      cut_half_length_px: 1,
      cut_step_px: 1,
      cutout_origin_x: 3,
      cutout_origin_y: 3,
      cutout: rampCutout(3, 3, 5, (x, y) => (x === 5 ? { 4: -20, 5: 0, 6: 900 }[y] : undefined)),
      moffat: {
        model: 'moffat', fitted: true, fwhm_px: 3.2, fwhm_arcsec: 2.4, beta: 2.8, r_squared: 0.998,
        curve_radius_px: [0, 1, 2], curve_values: [890, 480, -5],
      },
    });
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(screen.getByTestId('star-profile-plot-yaxis-type').textContent).toBe('linear');
    expect(plotData['star-profile-plot'][0].y).toEqual([-20, 0, 900]);
    const toggle = screen.getByRole('button', { name: 'Log scale' });
    expect(toggle.getAttribute('title')).toBe(LOG_SCALE_TOOLTIP);
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('star-profile-plot-yaxis-type').textContent).toBe('log');
    expect(screen.getByText('above sky, log')).toBeTruthy();
    const traces = plotData['star-profile-plot'];
    expect(traces[0].y).toEqual([3.5, 3.5, 900]);
    expect(traces[1].y).toEqual([3.5, 480, 890, 890, 480, 3.5]);
    // The half-height line stays at half the peak (890 / 2), above the floor.
    expect(traces[2].y).toEqual([445, 445]);
    // A log axis takes its range and annotation positions as powers of ten, spanning the data: 3.5 up to 1.5 x 900.
    const range = JSON.parse(screen.getByTestId('star-profile-plot-yaxis-range').textContent ?? 'null') as number[];
    expect(range[0]).toBeCloseTo(Math.log10(3.5), 6);
    expect(range[1]).toBeCloseTo(Math.log10(1350), 6);
    expect(range[1]).toBeLessThan(5);
    expect(Number(screen.getByTestId('star-profile-plot-annotation-y').textContent)).toBeCloseTo(Math.log10(445), 6);
  });

  it('gives the clipped reason as the saturation row tooltip, and the notes as one line', () => {
    const measurement = makeMeasurement({ clipped: true, clipped_reason: 'Three pixels share the peak value.', color_note: 'Color image: channels averaged.' });
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} pictureNote="Already stretched." />
    );
    expect(screen.getByTestId('star-profile-row-saturation').getAttribute('title')).toBe('Three pixels share the peak value.');
    expect(screen.getByTestId('star-profile-note').textContent).toBe('Color image: channels averaged. Already stretched.');
  });

  it('shows the reason in place of the width, and draws no fitted curve or FWHM line, for a profile that is not a star', () => {
    const measurement = makeMeasurement({ star_found: false, not_a_star_reason: 'Too flat to be a star.' });
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(screen.getByTestId('star-profile-width').textContent).toBe('Too flat to be a star.');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(plotData['star-profile-plot'].map((trace) => trace.name)).toEqual(['pixels along the cut']);
    expect(plotData['star-profile-plot'][0].x).toHaveLength(41);
  });

  it('falls back to the Gaussian fit, and says so', () => {
    const measurement = makeMeasurement({ moffat: { model: 'moffat', fitted: false, reason: 'Too few pixels.' } });
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(screen.getByTestId('star-profile-width').textContent).toBe('FWHM 3.00 px (2.25″) (Gaussian)');
    expect(plotData['star-profile-plot'][1].y[0]).toBe(180);
  });

  it('shows the reason when both fits failed, and no curve', () => {
    const measurement = makeMeasurement({
      moffat: { model: 'moffat', fitted: false, reason: 'Too few pixels above the noise.' },
      gaussian: { model: 'gaussian', fitted: false, reason: 'No fit.' },
    });
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    expect(screen.getByTestId('star-profile-width').textContent).toBe('Too few pixels above the noise.');
    expect(plotData['star-profile-plot'].map((trace) => trace.name)).toEqual(['pixels along the cut']);
  });
});

describe('the star inspector card', () => {
  const anchor = { x: 51, y: 120, firstRowAtTop: false, searchRadiusPx: 6 };
  const noop = () => undefined;
  /** A named star's record, with a catalog magnitude and a catalog type. */
  const identity = { id: 'Vega', name: 'Vega', magnitude: 0.03, hasCatalogMagnitude: true, spectralType: 'A0V' };

  it('shows one identity line: name, magnitude, type chip and a link that opens the star', () => {
    const open = vi.fn();
    render(
      <StarProfilePanel
        state={{ status: 'ready', anchor, measurement: makeMeasurement() }}
        cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop}
        starName="Vega" identity={identity} onOpenInManager={open}
      />
    );
    const line = screen.getByTestId('star-profile-identity');
    expect(line.textContent).toContain('Vega');
    expect(line.textContent).toContain('mag 0.03');
    expect(line.textContent).toContain('Catalog: A0V');
    fireEvent.click(screen.getByRole('button', { name: 'Open in Astronomy Manager' }));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('has no link when the star is not in the catalog', () => {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} starName="Unnamed star" />
    );
    expect(screen.queryByRole('button', { name: 'Open in Astronomy Manager' })).toBeNull();
  });

  it('lists Measurable here rows with yes or no, comparing the width with the frame typical', () => {
    const measurement = makeMeasurement({ circle_neighbor_count: 1, peak_over_sky_noise: 4 });
    render(
      <StarProfilePanel
        state={{ status: 'ready', anchor, measurement }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} frameWidthPx={3.0}
      />
    );
    expect(screen.getByTestId('star-profile-row-width').textContent).toContain('frame typical 3.0 px');
    expect(screen.getByTestId('star-profile-row-width').textContent).toContain('Yes');
    expect(screen.getByTestId('star-profile-row-signal').textContent).toContain('No');
    expect(screen.getByTestId('star-profile-row-neighbors').textContent).toContain('1 neighbor in the star circle');
    // No edge row for a star in the middle of the frame, and a Round row from the star's elongation.
    expect(screen.queryByTestId('star-profile-row-edge')).toBeNull();
    expect(screen.getByTestId('star-profile-row-shape').textContent).toContain('Round');
    expect(screen.getByTestId('star-profile-row-shape').textContent).toContain('elongation 1.08');
    expect(screen.getByTestId('star-profile-row-shape').textContent).toContain('Yes');
    expect(screen.getByTestId('star-profile-stats').textContent).toContain('Elong 1.08');
  });

  it('says Round: No with the long-axis direction for a stretched star, and gives no verdict for a faint one', () => {
    const { unmount } = render(
      <StarProfilePanel
        state={{ status: 'ready', anchor, measurement: makeMeasurement({ star_elongation: 1.5, star_elongation_angle_deg: 35 }) }}
        cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop}
      />
    );
    const stretched = screen.getByTestId('star-profile-row-shape').textContent ?? '';
    expect(stretched).toContain('No');
    expect(stretched).toContain('elongation 1.50, long axis at 35°');
    unmount();
    render(
      <StarProfilePanel
        state={{ status: 'ready', anchor, measurement: makeMeasurement({ star_elongation: null, star_elongation_angle_deg: null, star_shape_judgeable: false, star_shape_note: 'too faint', star_shape_detail: 'The star is too faint.' }) }}
        cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop}
      />
    );
    const faint = screen.getByTestId('star-profile-row-shape').textContent ?? '';
    expect(faint).toContain('not judged: too faint');
    expect(faint).not.toContain('Yes');
    expect(screen.getByTestId('star-profile-stats').textContent).not.toContain('Elong');
  });

  it('opens the ring view behind the Optics detail link and closes it again', () => {
    render(
      <StarProfilePanel state={{ status: 'ready', anchor, measurement: makeMeasurement() }} cutAngleDegrees={0} onCutAngleChange={noop} onClose={noop} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Optics detail' }));
    expect(screen.getByTestId('star-profile-ring-plot')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Hide optics detail' }));
    expect(screen.queryByTestId('star-profile-ring-plot')).toBeNull();
  });
});

describe('Detections toggle', () => {
  it('is a pressed button with the tooltip and reports the new setting', () => {
    const onChange = vi.fn();
    render(<DetectionsToggle enabled={false} onChange={onChange} />);
    const button = screen.getByRole('button', { name: 'Detections' });
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(button.getAttribute('title')).toBe(DETECTIONS_TOGGLE_TOOLTIP);
    fireEvent.click(button);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('is grey when off and blue (pressed) when on', () => {
    const { rerender } = render(<DetectionsToggle enabled={false} onChange={() => undefined} />);
    const off = screen.getByRole('button', { name: 'Detections' });
    expect(off.className).toContain('btn--secondary');
    expect(off.className).not.toContain('btn--active');
    rerender(<DetectionsToggle enabled onChange={() => undefined} />);
    const on = screen.getByRole('button', { name: 'Detections' });
    expect(on.className).not.toContain('btn--secondary');
    expect(on.className).toContain('btn--active');
    expect(on.getAttribute('aria-pressed')).toBe('true');
  });
});
