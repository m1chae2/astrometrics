/**
 * @fileoverview Unit test suite for TrackingRiskOverlay.
 *
 * Verifies that the mount tracking risk overlay samples altitudes up to 90.0 degrees (Zenith),
 * ensuring seamless coverage of the celestial dome without leaving an unrendered hole,
 * and verifies that cumulative multi-session tracking attempts are ingested and rendered.
 */

import { describe, it, expect, vi } from 'vitest';
import { TrackingRiskOverlay, rmsToRiskScore } from '../planetariumDisplay/layers/TrackingRiskOverlay';
import { ProjectionContext } from '../planetariumDisplay/layers/overlayTypes';

describe('TrackingRiskOverlay', () => {
  /**
   * Tests that TrackingRiskOverlay skips drawing when showTrackingRisk flag is false.
   */
  it('does not draw when showTrackingRisk is false', () => {
    const overlay = new TrackingRiskOverlay();
    const mockContext = {
      save: vi.fn(),
      restore: vi.fn(),
    } as unknown as CanvasRenderingContext2D;

    const mockProjection = {
      showTrackingRisk: false,
    } as unknown as ProjectionContext;

    overlay.draw(mockContext, mockProjection);
    expect(mockContext.save).not.toHaveBeenCalled();
  });

  /**
   * Tests that TrackingRiskOverlay samples altitude up to 90.0 degrees (Zenith)
   * to ensure no dark gap is left at the center of the dome.
   */
  it('samples altitudes up to 90.0 degrees to fully seal the zenith', () => {
    const overlay = new TrackingRiskOverlay();
    const mockContext = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      stroke: vi.fn(),
      roundRect: vi.fn(),
      fillText: vi.fn(),
      fillRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D;

    const sampledAltitudes: number[] = [];

    const mockProjection = {
      showTrackingRisk: true,
      lst: 310,
      width: 1600,
      height: 900,
      alignmentAttempts: [],
      getRaDec: vi.fn((alt: number, _az: number) => {
        sampledAltitudes.push(alt);
        return { ra: 310, dec: 39.7392 };
      }),
      projectCoords: vi.fn(() => ({
        x: 800,
        y: 450,
        visible: true,
        alt: 90,
      })),
    } as unknown as ProjectionContext;

    overlay.draw(mockContext, mockProjection);

    expect(mockContext.save).toHaveBeenCalled();
    const maxSampledAltitude = Math.max(...sampledAltitudes);
    expect(maxSampledAltitude).toBe(90.0);
  });

  /**
   * Tests that TrackingRiskOverlay prefers cumulativeTrackingAttempts across sessions
   * and renders the multi-session legend badge title.
   */
  it('prefers cumulativeTrackingAttempts and displays multi-session legend title', () => {
    const overlay = new TrackingRiskOverlay();
    const fillTextMock = vi.fn();
    const mockContext = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      stroke: vi.fn(),
      roundRect: vi.fn(),
      fillText: fillTextMock,
      fillRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D;

    const mockProjection = {
      showTrackingRisk: true,
      lst: 310,
      width: 1600,
      height: 900,
      alignmentAttempts: [
        // Single session attempt (should be superseded by cumulative)
        { status: 'aligned', ra: 100, dec: 20, deltaRaArcsec: 0.1, deltaDecArcsec: 0.1, timestamp: 1000 },
      ],
      cumulativeTrackingAttempts: [
        // Multi-session target 1: Deneb
        { status: 'aligned', targetName: 'Deneb', ra: 310.4, dec: 45.3, deltaRaArcsec: 0.2, deltaDecArcsec: 0.1, timestamp: 1000 },
        { status: 'aligned', targetName: 'Deneb', ra: 310.4, dec: 45.3, deltaRaArcsec: 0.3, deltaDecArcsec: -0.2, timestamp: 1005 },
        // Multi-session target 2: Vega
        { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 0.8, deltaDecArcsec: 0.7, timestamp: 2000 },
        { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 0.9, deltaDecArcsec: -0.6, timestamp: 2005 },
      ],
      getRaDec: vi.fn(() => ({ ra: 310, dec: 39.7392 })),
      projectCoords: vi.fn(() => ({
        x: 800,
        y: 450,
        visible: true,
        alt: 90,
      })),
    } as unknown as ProjectionContext;

    overlay.draw(mockContext, mockProjection);

    expect(mockContext.save).toHaveBeenCalled();
    const renderedTitles = fillTextMock.mock.calls.map((call) => call[0]);
    expect(renderedTitles).toContain('Tracking Heatmap (2 targets · 4 solves)');
  });

  /**
   * Tests that rmsToRiskScore properly normalizes tracking errors against equipment plate scale.
   */
  it('scales tracking risk score relative to imaging plate scale', () => {
    // Equipment rig: 75mm f/5.4 (405mm) + 3.76um sensor -> ~1.91"/px plate scale
    const plateScale = 1.91;

    // Up to 0.75x plate scale (<=1.43" RMS) yields round stars on 5-min subs -> strictly green (<0.28)
    const alkaidScore = rmsToRiskScore(0.82, plateScale);
    const mirachScore = rmsToRiskScore(0.87, plateScale);
    const schedarScore = rmsToRiskScore(0.93, plateScale);
    const naviScore = rmsToRiskScore(1.0, plateScale);
    const m81Score = rmsToRiskScore(1.2, plateScale);
    expect(alkaidScore).toBeLessThan(0.28);
    expect(mirachScore).toBeLessThan(0.28);
    expect(schedarScore).toBeLessThan(0.28);
    expect(naviScore).toBeLessThan(0.28);
    expect(m81Score).toBeLessThan(0.28);

    // Acceptable guiding between 0.75x and 1.25x plate scale (1.43" to 2.39") -> amber (0.28 to 0.57)
    const cautionScore = rmsToRiskScore(1.8, plateScale);
    expect(cautionScore).toBeGreaterThanOrEqual(0.28);
    expect(cautionScore).toBeLessThan(0.58);

    // Severe trailing (>2.39" RMS) -> red (>=0.58 risk)
    const trailingScore = rmsToRiskScore(2.8, plateScale);
    expect(trailingScore).toBeGreaterThanOrEqual(0.58);
  });

  /**
   * Tests that rmsToRiskScore uses sensible real-world fallback thresholds when no plate scale is configured.
   */
  it('uses realistic amateur mount thresholds when plate scale is undefined', () => {
    // <= 1.2" RMS should be green (<0.28)
    expect(rmsToRiskScore(0.8)).toBeLessThan(0.28);
    expect(rmsToRiskScore(1.2)).toBeLessThanOrEqual(0.28);

    // 1.2" to 2.0" RMS should be amber (0.28 to 0.57)
    const midScore = rmsToRiskScore(1.6);
    expect(midScore).toBeGreaterThanOrEqual(0.28);
    expect(midScore).toBeLessThan(0.58);

    // > 2.0" RMS should be red (>=0.58)
    const highScore = rmsToRiskScore(2.4);
    expect(highScore).toBeGreaterThanOrEqual(0.58);
  });

  /**
   * Tests that rmsToRiskScore correctly penalizes small RMS on high-magnification narrow-field rigs.
   */
  it('penalizes modest RMS on narrow-field planetary or SCT rigs', () => {
    // 2000mm focal length SCT with 0.40"/px plate scale
    const sctPlateScale = 0.4;

    // 0.8" RMS is 2 full pixels of blur -> must be red (>=0.58)
    const sctRisk = rmsToRiskScore(0.8, sctPlateScale);
    expect(sctRisk).toBeGreaterThanOrEqual(0.58);
  });
});
