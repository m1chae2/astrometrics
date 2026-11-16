/**
 * @file test_pixelReadout.test.tsx
 * @description Tests the pixel readout line: the question sent to the worker, the 1-based position,
 * the channel values, and that RA and Dec appear only when the header has a plate solution.
 */
import React, { useRef } from 'react';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PixelReadout, formatChannelValues, formatPixelValue } from '../PixelReadout';
import type { ParsedFitsData } from '../hooks/useFitsLoader';

/** A stand-in for the FITS worker that answers every pixel question with a fixed result. */
class FakeWorker extends EventTarget {
  /** Every message the readout posted. */
  posted: Array<Record<string, unknown>> = [];

  /** The values the worker reports for the pixel. */
  values: number[] = [1234];

  /**
   * Records the question and answers it, as the real worker does.
   *
   * @param message The question.
   */
  postMessage(message: Record<string, unknown>): void {
    this.posted.push(message);
    const column = message.column as number;
    const rowFromTop = message.rowFromTop as number;
    this.dispatchEvent(new MessageEvent('message', {
      data: {
        cmd: 'pixelComplete',
        requestId: message.requestId,
        imageId: message.imageId,
        result: { x: column + 1, y: 160 - rowFromTop, values: this.values },
      },
    }));
  }
}

/** The header of the M 31 stack: a plate solution with SIP terms. */
const SOLVED_HEADER = {
  CTYPE1: 'RA---TAN-SIP',
  CTYPE2: 'DEC--TAN-SIP',
  CRPIX1: 1685.93223572,
  CRPIX2: 1206.48699951,
  CRVAL1: 10.8810120906,
  CRVAL2: 41.1458844842,
  PC1_1: 0.000514216294499,
  PC1_2: -0.000142867911728,
  PC2_1: 0.000142892438101,
  PC2_2: 0.000514095720772,
  CDELT1: 1.0,
  CDELT2: 1.0,
  A_ORDER: 2,
  B_ORDER: 2,
};

/**
 * Makes the record of a decoded image.
 *
 * @param header The header cards to attach.
 * @param channels Number of channels.
 * @returns The record.
 */
function makeImage(header: ParsedFitsData['header'], channels = 1): ParsedFitsData {
  return { imageId: 5, w: 200, h: 160, channels, min: 0, max: 65535, header };
}

/**
 * Renders the readout over a container and moves the pointer over it.
 *
 * @param worker The fake worker.
 * @param parsedData The decoded image, or null.
 * @returns The container element, to send pointer events to.
 */
function renderReadout(worker: FakeWorker, parsedData: ParsedFitsData | null): HTMLElement {
  const Harness: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const workerRef = useRef<Worker | null>(worker as unknown as Worker);
    return (
      <div>
        <div ref={containerRef} data-testid="container" />
        <PixelReadout
          containerRef={containerRef}
          workerRef={workerRef}
          parsedData={parsedData}
          drawnSize={{ w: 100, h: 80 }}
          zoom={2}
          panX={0}
          panY={0}
        />
      </div>
    );
  };
  render(<Harness />);
  return screen.getByTestId('container');
}

/**
 * Moves the pointer over an element.
 *
 * @param element The element.
 * @param x Pointer x in screen pixels from the element's left edge.
 * @param y Pointer y in screen pixels from the element's top edge.
 */
function movePointer(element: HTMLElement, x: number, y: number): void {
  act(() => {
    element.dispatchEvent(new MouseEvent('pointermove', { clientX: x, clientY: y, bubbles: true }));
  });
}

describe('PixelReadout', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks the worker for the file pixel under the pointer and shows its 1-based position and value', () => {
    const worker = new FakeWorker();
    const container = renderReadout(worker, makeImage(undefined));
    movePointer(container, 50, 40);
    // Screen (50, 40) at zoom 2 is drawn point (25, 20); the drawn picture is half the file's size.
    expect(worker.posted).toHaveLength(1);
    expect(worker.posted[0]).toMatchObject({ cmd: 'pixel', imageId: 5, column: 50, rowFromTop: 40 });
    expect(screen.getByText('x 51 y 120')).toBeTruthy();
    expect(screen.getByText('1234')).toBeTruthy();
    expect(screen.getByText('x 51 y 120').getAttribute('title')).toContain('starts at 1');
  });

  it('shows the value in ADU, by the rule for the file type, with its own tooltip', () => {
    const worker = new FakeWorker();
    worker.values = [0.5];
    const container = renderReadout(worker, makeImage({ BITPIX: -32 }));
    movePointer(container, 50, 40);
    const badge = screen.getByText('32768 ADU');
    expect(badge.getAttribute('title')).toContain('65,535');
  });

  it('shows the three channel values of a color image', () => {
    const worker = new FakeWorker();
    worker.values = [10, 20.5, 30];
    const container = renderReadout(worker, makeImage(undefined, 3));
    movePointer(container, 10, 10);
    expect(screen.getByText('R 10 G 20.5 B 30')).toBeTruthy();
  });

  it('shows RA and Dec when the header has a plate solution', () => {
    const worker = new FakeWorker();
    const container = renderReadout(worker, makeImage(SOLVED_HEADER));
    movePointer(container, 50, 40);
    const sky = screen.getByTestId('pixel-readout-sky');
    expect(sky.textContent).toMatch(/^RA \d\dh \d\dm \d\d\.\d\ds {2}Dec [+-]\d\d° \d\d′ \d\d\.\d″$/);
  });

  it('hides RA and Dec when the header has no plate solution', () => {
    const worker = new FakeWorker();
    // Sky-map cards from a camera program: where the mount pointed, not a solution.
    const pointingOnly = { ...SOLVED_HEADER, A_ORDER: undefined, B_ORDER: undefined } as unknown as ParsedFitsData['header'];
    const container = renderReadout(worker, makeImage(pointingOnly));
    movePointer(container, 50, 40);
    expect(screen.getByText('x 51 y 120')).toBeTruthy();
    expect(screen.queryByTestId('pixel-readout-sky')).toBeNull();
  });

  it('shows no value when the pointer is outside the drawn image', () => {
    const worker = new FakeWorker();
    const container = renderReadout(worker, makeImage(undefined));
    movePointer(container, 500, 40);
    expect(worker.posted).toHaveLength(0);
    expect(screen.queryByText(/^x \d/)).toBeNull();
  });

  it('shows nothing, and asks the worker nothing, while the image holds no decoded data', () => {
    const worker = new FakeWorker();
    const container = renderReadout(worker, null);
    movePointer(container, 50, 40);
    expect(screen.queryByTestId('pixel-readout')).toBeNull();
    expect(worker.posted).toHaveLength(0);
  });
});

describe('pixel value text', () => {
  it('keeps whole numbers whole and trims long fractions', () => {
    expect(formatPixelValue(1234)).toBe('1234');
    expect(formatPixelValue(0.0123456789)).toBe('0.0123457');
    expect(formatPixelValue(NaN)).toBe('NaN');
    expect(formatChannelValues([1, 2, 3])).toBe('R 1  G 2  B 3');
  });
});
