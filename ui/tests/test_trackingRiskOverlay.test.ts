/**
 * @fileoverview Unit test suite for TrackingRiskOverlay.
 *
 * The risk scores come from the backend (the performance envelope's
 * `trackingRisk` grid; the scoring rules are tested in
 * `wayfindinglib/analytics/test/test_tracking_risk.py`). These tests check
 * the display side: the dome is sampled up to the zenith, grid lookups blend
 * neighboring points, and the legend reports what the grid was built from.
 */

import { describe, it, expect, vi } from 'vitest';
import { TrackingRiskOverlay, scoreAt } from '../planetariumDisplay/layers/TrackingRiskOverlay';
import { ProjectionContext } from '../planetariumDisplay/layers/overlayTypes';
import { TrackingRiskMap } from '../common/types/backendTypes';

/**
 * Builds a small grid whose score rises with hour angle.
 *
 * @param {Partial<TrackingRiskMap>} overrides - Fields to change.
 * @returns {TrackingRiskMap} The grid.
 */
function makeRiskMap(overrides: Partial<TrackingRiskMap> = {}): TrackingRiskMap {
  const haDeg = [-180, -90, 0, 90, 180];
  const decDeg = [-90, 0, 90];
  return {
    latitudeDeg: 40,
    haDeg,
    decDeg,
    scores: decDeg.map(() => haDeg.map((ha) => (ha + 180) / 400)),
    cautionScore: 0.28,
    highScore: 0.58,
    idealRmsArcsec: 1.4,
    trailingRmsArcsec: 2.4,
    plateScaleArcsecPerPx: 1.91,
    measuredTargetCount: 0,
    solveCount: 0,
    ...overrides,
  };
}

/**
 * Builds a canvas context whose drawing calls are all spies.
 *
 * @param {ReturnType<typeof vi.fn>} fillText - Spy for text drawing.
 * @returns {CanvasRenderingContext2D} The stand-in context.
 */
function makeContext(fillText = vi.fn()): CanvasRenderingContext2D {
  return {
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    stroke: vi.fn(),
    roundRect: vi.fn(),
    fillText,
    fillRect: vi.fn(),
  } as unknown as CanvasRenderingContext2D;
}

/**
 * Builds a projection that maps every sky point to the screen center.
 *
 * @param {TrackingRiskMap | null} trackingRisk - The grid, or none.
 * @param {number[]} sampledAltitudes - Collects the altitudes asked for.
 * @returns {ProjectionContext} The stand-in projection.
 */
function makeProjection(trackingRisk: TrackingRiskMap | null, sampledAltitudes: number[] = []): ProjectionContext {
  return {
    showTrackingRisk: true,
    lst: 310,
    width: 1600,
    height: 900,
    trackingRisk,
    getRaDec: vi.fn((alt: number) => {
      sampledAltitudes.push(alt);
      return { ra: 310, dec: 39.7392 };
    }),
    projectCoords: vi.fn(() => ({ x: 800, y: 450, visible: true, alt: 90 })),
  } as unknown as ProjectionContext;
}

describe('TrackingRiskOverlay', () => {
  /** Nothing is drawn while the overlay is off. */
  it('does not draw when showTrackingRisk is false', () => {
    const overlay = new TrackingRiskOverlay();
    const context = makeContext();
    overlay.draw(context, { showTrackingRisk: false } as unknown as ProjectionContext);
    expect(context.save).not.toHaveBeenCalled();
  });

  /** The dome is sampled up to 90 degrees so no gap is left at the zenith. */
  it('samples altitudes up to 90.0 degrees to fully seal the zenith', () => {
    const sampled: number[] = [];
    new TrackingRiskOverlay().draw(makeContext(), makeProjection(makeRiskMap(), sampled));
    expect(Math.max(...sampled)).toBe(90.0);
  });

  /** Grid lookups blend the nearest points and clamp at the edges. */
  it('blends neighboring grid points', () => {
    const riskMap = makeRiskMap();
    expect(scoreAt(riskMap, 0, 0)).toBeCloseTo(0.45);
    expect(scoreAt(riskMap, 45, 30)).toBeCloseTo((0.45 + 0.675) / 2);
    expect(scoreAt(riskMap, 180, 90)).toBeCloseTo(0.9);
    expect(scoreAt(riskMap, 500, -500)).toBeCloseTo(0.9);
  });

  /** The legend names the measured targets and the grid's jitter limits. */
  it('reports the measured targets and jitter limits in the legend', () => {
    const fillText = vi.fn();
    const riskMap = makeRiskMap({ measuredTargetCount: 2, solveCount: 4 });
    new TrackingRiskOverlay().draw(makeContext(fillText), makeProjection(riskMap));
    const texts = fillText.mock.calls.map((call) => call[0]);
    expect(texts).toContain('Tracking Heatmap (2 targets · 4 solves)');
    expect(texts).toContain('≤1.4" / Round');
    expect(texts).toContain('>2.4" / Trailing');
  });

  /** Without a grid (no active equipment) only the legend says so. */
  it('draws no mesh without a grid', () => {
    const fillText = vi.fn();
    const context = makeContext(fillText);
    new TrackingRiskOverlay().draw(context, makeProjection(null));
    expect(context.moveTo).not.toHaveBeenCalled();
    expect(fillText.mock.calls.map((call) => call[0])).toContain(
      'Tracking Heatmap (no active telescope and camera)'
    );
  });
});
