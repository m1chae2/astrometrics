/**
 * @file PixelReadout.tsx
 * @description A one-line readout floating over the bottom-left corner of a FITS image, shown while the pointer is on it: the pixel under the pointer, its value in the
 * original data, and its place on the sky when the image has a plate solution.
 *
 * The decoded pixels live in the FITS worker, so each reading is a short question to the worker
 * ("what is the value at column c, row r?"). Questions are sent at most once per animation frame, and an
 * answer that arrives after a newer question was sent is ignored.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge } from '../components/Badge';
import type { ParsedFitsData } from './hooks/useFitsLoader';
import { newPixelRequestId, pointerToFileCell } from './utils/pixelQuery';
import type { PixelReading } from './utils/pixelQuery';
import { describePixelValues } from './utils/pixelValueUnits';
import { createPixelToSky, formatDeclination, formatRightAscension } from './wcs';
import type { SkyPosition } from './wcs';

/** The tooltip of the position badge. */
export const PIXEL_POSITION_TOOLTIP =
  'Pixel column (x) and row (y) under the pointer. Counting starts at 1, as in FITS and DS9, so the first pixel is 1, 1.';

/** The tooltip of the value badge. */
export const PIXEL_VALUE_TOOLTIP =
  'Value of this pixel in the original data, before any brightness scaling. A color image shows its red, green and blue values.';

/** The tooltip of the sky badge. */
export const PIXEL_SKY_TOOLTIP =
  'Right ascension and declination at the center of this pixel, from the plate solution in the FITS header.';

/** Where the pointer is and what the worker last said about it. */
interface ReadoutState {
  reading: PixelReading;
  sky: SkyPosition | null;
}

/**
 * Writes a pixel value with up to six significant digits, so a count of 1234 stays whole and
 * a float such as 0.0123456789 stays short.
 *
 * @param value The pixel value.
 * @returns The text.
 */
export function formatPixelValue(value: number): string {
  if (Number.isNaN(value)) return 'NaN';
  if (!Number.isFinite(value)) return value > 0 ? '+Inf' : '-Inf';
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toPrecision(6)));
}

/**
 * Writes the value part of the readout: one number for gray, or R, G and B for color.
 *
 * @param values The channel values.
 * @returns The text.
 */
export function formatChannelValues(values: readonly number[]): string {
  if (values.length === 3) {
    return `R ${formatPixelValue(values[0])}  G ${formatPixelValue(values[1])}  B ${formatPixelValue(values[2])}`;
  }
  return values.map(formatPixelValue).join('  ');
}

/** Props for {@link PixelReadout}. */
export interface PixelReadoutProps {
  /** The element that holds the image; pointer moves over it are read. */
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** The FITS worker that holds the decoded pixels. */
  workerRef: React.RefObject<Worker | null>;
  /** The decoded image, or null while there is none (the readout then shows nothing). */
  parsedData: ParsedFitsData | null;
  /** Size of the drawn image, in the units the pan and zoom move it by. */
  drawnSize: { w: number; h: number };
  /** Zoom of the image on screen. */
  zoom: number;
  /** Horizontal offset of the image's left edge from the container's left edge, in screen pixels. */
  panX: number;
  /** Vertical offset of the image's top edge from the container's top edge, in screen pixels. */
  panY: number;
}

/**
 * Shows the pixel readout line and keeps it up to date while the pointer moves over the image.
 *
 * @param props See {@link PixelReadoutProps}.
 * @returns The readout line, an empty hidden element while the pointer is off the image, or null when there is no decoded image and no note to show.
 */
export const PixelReadout: React.FC<PixelReadoutProps> = ({
  containerRef, workerRef, parsedData, drawnSize, zoom, panX, panY,
}) => {
  const [state, setState] = useState<ReadoutState | null>(null);
  const pixelToSky = useMemo(
    () => (parsedData?.header ? createPixelToSky(parsedData.header) : null),
    [parsedData],
  );

  // What the animation-frame callback needs, kept in refs so it always sees the latest values.
  const latestRef = useRef({ parsedData, drawnSize, zoom, panX, panY, pixelToSky });
  useEffect(() => {
    latestRef.current = { parsedData, drawnSize, zoom, panX, panY, pixelToSky };
  });
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const frameRef = useRef<number | null>(null);
  const lastRequestIdRef = useRef(0);

  /** Forgets the pointer and the reading. */
  const clear = useCallback(() => {
    pointerRef.current = null;
    lastRequestIdRef.current = 0;
    setState(null);
  }, []);

  /** Sends the newest pointer position to the worker; runs once per animation frame at most. */
  const askWorker = useCallback(() => {
    frameRef.current = null;
    const pointer = pointerRef.current;
    const worker = workerRef.current;
    const { parsedData: image, drawnSize: size, zoom: currentZoom, panX: currentPanX, panY: currentPanY } = latestRef.current;
    if (!pointer || !worker || !image) return;
    const cell = pointerToFileCell(pointer.x, pointer.y, image, size, currentZoom, currentPanX, currentPanY);
    if (!cell) {
      lastRequestIdRef.current = 0;
      setState(null);
      return;
    }
    const requestId = newPixelRequestId();
    lastRequestIdRef.current = requestId;
    worker.postMessage({
      cmd: 'pixel',
      requestId,
      imageId: image.imageId,
      column: cell.column,
      rowFromTop: cell.rowFromTop,
    });
  }, [workerRef]);

  // Read the pointer from the container and the answers from the worker.
  useEffect(() => {
    const container = containerRef.current;
    const worker = workerRef.current;
    if (!container || !worker || !parsedData) {
      clear();
      return undefined;
    }
    const onPointerMove = (event: PointerEvent) => {
      const rect = container.getBoundingClientRect();
      pointerRef.current = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      if (frameRef.current === null) frameRef.current = requestAnimationFrame(askWorker);
    };
    const onPointerLeave = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      clear();
    };
    const onMessage = (event: MessageEvent) => {
      if (event.data?.cmd !== 'pixelComplete' || event.data.requestId !== lastRequestIdRef.current) return;
      const result = event.data.result as PixelReading | null;
      if (!result) {
        setState(null);
        return;
      }
      const sky = latestRef.current.pixelToSky ? latestRef.current.pixelToSky(result.x, result.y) : null;
      setState({ reading: result, sky });
    };
    container.addEventListener('pointermove', onPointerMove);
    container.addEventListener('pointerleave', onPointerLeave);
    worker.addEventListener('message', onMessage);
    return () => {
      container.removeEventListener('pointermove', onPointerMove);
      container.removeEventListener('pointerleave', onPointerLeave);
      worker.removeEventListener('message', onMessage);
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      clear();
    };
  }, [containerRef, workerRef, parsedData, askWorker, clear]);

  if (!parsedData) {
    return null;
  }

  // Over the image, the readout shows only while the pointer is on it.
  if (!state) return <div data-testid="pixel-readout" hidden />;
  const valueText = describePixelValues(state.reading.values, parsedData.header);

  return (
    <div className="fits-renderer__readout" data-testid="pixel-readout">
      <Badge opaque title={PIXEL_POSITION_TOOLTIP}>{`x ${state.reading.x}  y ${state.reading.y}`}</Badge>
      <Badge opaque title={valueText.tooltip}>{valueText.text}</Badge>
      {state.sky && (
        <Badge opaque title={PIXEL_SKY_TOOLTIP} data-testid="pixel-readout-sky">
          {`RA ${formatRightAscension(state.sky.raDegrees)}  Dec ${formatDeclination(state.sky.decDegrees)}`}
        </Badge>
      )}
    </div>
  );
};
