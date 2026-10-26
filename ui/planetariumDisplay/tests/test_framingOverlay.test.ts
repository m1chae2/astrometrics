/**
 * @fileoverview Tests that the framing overlay draws exactly the corners it
 * is given, through the map's own projection, and nothing else.
 */

import { describe, expect, it, vi } from 'vitest';
import { FramingOverlay } from '../layers/FramingOverlay';
import type { ProjectionContext } from '../layers/overlayTypes';

/**
 * Builds a fake canvas context that records the path it is asked to draw.
 * @returns The context and the recorded calls.
 */
function makeContext() {
  const calls = { moveTo: [] as number[][], lineTo: [] as number[][], text: [] as string[] };
  const context = {
    save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), closePath: vi.fn(), fill: vi.fn(), stroke: vi.fn(),
    moveTo: (x: number, y: number) => calls.moveTo.push([x, y]),
    lineTo: (x: number, y: number) => calls.lineTo.push([x, y]),
    fillText: (text: string) => calls.text.push(text),
  } as unknown as CanvasRenderingContext2D;
  return { context, calls };
}

/**
 * Builds a projection context whose projection maps RA to x and Dec to y.
 * @param framingPanels The panels to draw.
 * @param visible Whether every point counts as in view.
 * @returns The context.
 */
function makeProjection(framingPanels: ProjectionContext['framingPanels'], visible = true): ProjectionContext {
  return {
    framingPanels,
    projectCoords: (ra: number, dec: number) => ({ x: ra, y: dec, visible, alt: 10 }),
  } as unknown as ProjectionContext;
}

const squarePanel = {
  name: 'P1',
  corners: [
    { raDeg: 10, decDeg: 20 }, { raDeg: 12, decDeg: 20 }, { raDeg: 12, decDeg: 22 }, { raDeg: 10, decDeg: 22 },
  ],
};

describe('FramingOverlay', () => {
  /** The outline follows the given corners in the given order. */
  it('draws the given corners through the projection', () => {
    const { context, calls } = makeContext();
    new FramingOverlay().draw(context, makeProjection([squarePanel]));
    expect(calls.moveTo).toEqual([[10, 20]]);
    expect(calls.lineTo).toEqual([[12, 20], [12, 22], [10, 22]]);
    expect(calls.text).toEqual(['P1']);
  });

  /** With no panels nothing is drawn. */
  it('draws nothing without panels', () => {
    const { context } = makeContext();
    new FramingOverlay().draw(context, makeProjection(undefined));
    expect(context.beginPath).not.toHaveBeenCalled();
  });

  /** A panel with a corner out of view is skipped, not guessed. */
  it('skips a panel that is out of view', () => {
    const { context } = makeContext();
    new FramingOverlay().draw(context, makeProjection([squarePanel], false));
    expect(context.beginPath).not.toHaveBeenCalled();
  });
});
