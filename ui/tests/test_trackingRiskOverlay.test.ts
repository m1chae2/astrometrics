/**
 * @fileoverview Unit test suite for TrackingRiskOverlay.
 *
 * Verifies that the mount tracking risk overlay samples altitudes up to 90.0 degrees (Zenith),
 * ensuring seamless coverage of the celestial dome without leaving an unrendered hole,
 * and verifies that cumulative multi-session tracking attempts are ingested and rendered.
 */

import { describe, it, expect, vi } from 'vitest';
import { TrackingRiskOverlay } from '../planetariumDisplay/layers/TrackingRiskOverlay';
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
});
