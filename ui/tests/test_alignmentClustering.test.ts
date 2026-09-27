/**
 * @fileoverview Unit test suite for alignmentClustering utility.
 *
 * Verifies that plate-solve alignment clustering:
 * 1. Computes tracking RMS as jitter/dispersion around the settled pointing position,
 *    rather than treating large absolute catalog framing offsets as tracking error.
 * 2. Properly segments multi-night or multi-session observations with large time gaps,
 *    summing actual elapsed exposure durations rather than span timestamps across months.
 * 3. Pools tracking dispersion across multiple segmented runs on the same celestial target.
 */

import { describe, it, expect } from 'vitest';
import { clusterAlignmentAttempts, formatDuration } from '../planetariumDisplay/utils/alignmentClustering';
import { AlignmentAttempt } from '../common/types/backendTypes';

describe('alignmentClustering', () => {
  /**
   * Tests that tracking RMS measures dispersion around the settled mean,
   * not the absolute offset from the catalog position.
   */
  it('measures tracking jitter around the settled mean rather than absolute catalog offset', () => {
    // Simulate 5 frames on Vega where the framing is intentionally offset 50 arcseconds
    // from the catalog center (e.g. framing a field), but guiding jitter is rock-solid (+- 0.5").
    const rawAttempts: AlignmentAttempt[] = [
      { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 50.0, deltaDecArcsec: 0.0, timestamp: 1000 },
      { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 50.5, deltaDecArcsec: 0.3, timestamp: 1010 },
      { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 49.5, deltaDecArcsec: -0.3, timestamp: 1020 },
      { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 50.2, deltaDecArcsec: 0.1, timestamp: 1030 },
      { status: 'aligned', targetName: 'Vega', ra: 279.2, dec: 38.8, deltaRaArcsec: 49.8, deltaDecArcsec: -0.1, timestamp: 1040 },
    ];

    const clusters = clusterAlignmentAttempts(rawAttempts);
    expect(clusters).toHaveLength(1);

    const cluster = clusters[0];
    expect(cluster.targetName).toBe('Vega');
    expect(cluster.totalFrames).toBe(5);

    // Initial slew error should preserve the initial ~50" pointing offset
    expect(cluster.initialErrorArcsec).toBeCloseTo(50.0, 1);

    // Tracking RMS must reflect the ~0.5" guiding dispersion, NOT the ~50" catalog framing offset
    expect(cluster.rmsTotal).toBeLessThan(1.0);
    expect(cluster.rmsTotal).toBeGreaterThan(0.2);
  });

  /**
   * Tests that sessions separated by hours/days are segmented, summing true exposure time
   * rather than reporting multi-month spans between separate dates.
   */
  it('segments runs across multi-hour gaps and sums cumulative exposure durations', () => {
    // Night 1: 3 frames over 60 seconds (timestamps 1,000 to 1,060)
    // Night 2: 3 frames over 60 seconds (timestamps 100,000 to 100,060 - 27+ hours later)
    const rawAttempts: AlignmentAttempt[] = [
      // Night 1 (settled around deltaRa=10.0, deltaDec=5.0 with low jitter)
      { status: 'aligned', targetName: 'M 27', ra: 300.0, dec: 22.7, deltaRaArcsec: 10.0, deltaDecArcsec: 5.0, timestamp: 1000 },
      { status: 'aligned', targetName: 'M 27', ra: 300.0, dec: 22.7, deltaRaArcsec: 10.4, deltaDecArcsec: 5.2, timestamp: 1030 },
      { status: 'aligned', targetName: 'M 27', ra: 300.0, dec: 22.7, deltaRaArcsec: 9.6, deltaDecArcsec: 4.8, timestamp: 1060 },

      // Night 2 (different framing setup: settled around deltaRa=-20.0, deltaDec=15.0 with low jitter)
      { status: 'aligned', targetName: 'M 27', ra: 300.0, dec: 22.7, deltaRaArcsec: -20.0, deltaDecArcsec: 15.0, timestamp: 100000 },
      { status: 'aligned', targetName: 'M 27', ra: 300.0, dec: 22.7, deltaRaArcsec: -19.6, deltaDecArcsec: 15.3, timestamp: 100030 },
      { status: 'aligned', targetName: 'M 27', ra: 300.0, dec: 22.7, deltaRaArcsec: -20.4, deltaDecArcsec: 14.7, timestamp: 100060 },
    ];

    const clusters = clusterAlignmentAttempts(rawAttempts);
    expect(clusters).toHaveLength(1);

    const cluster = clusters[0];
    expect(cluster.totalFrames).toBe(6);

    // Elapsed exposure time should be 60s + 60s = 120s (2m 0s), NOT 99,060s (27.5 hours)
    expect(cluster.elapsedSeconds).toBe(120);
    expect(formatDuration(cluster.elapsedSeconds)).toBe('2m 0s');

    // Pooled tracking RMS should measure sub-pixel guiding within each run,
    // not the 30" framing difference between Night 1 and Night 2
    expect(cluster.rmsTotal).toBeLessThan(1.0);
    expect(cluster.rmsTotal).toBeGreaterThan(0.2);
  });

  /**
   * Tests that single sync points remain non-tracking clusters with zero tracking RMS.
   */
  it('handles single sync points without corrupting tracking statistics', () => {
    const rawAttempts: AlignmentAttempt[] = [
      { status: 'aligned', targetName: 'Sync #1', ra: 150.0, dec: 60.0, deltaRaArcsec: 14.2, deltaDecArcsec: -8.1, timestamp: 500 },
    ];

    const clusters = clusterAlignmentAttempts(rawAttempts);
    expect(clusters).toHaveLength(1);
    expect(clusters[0].totalFrames).toBe(1);
    expect(clusters[0].rmsTotal).toBe(0);
    expect(clusters[0].initialErrorArcsec).toBeCloseTo(Math.hypot(14.2, -8.1), 1);
  });

  /**
   * Tests that celestial targets with Right Ascension between 0 and 24 degrees
   * (e.g. Navi at 14.18° and Schedar at 10.13° in Cassiopeia) are preserved in
   * decimal degrees and NOT erroneously multiplied by 15.
   */
  it('preserves decimal degree RA for objects in the first hour of RA (0 to 24 deg)', () => {
    // Navi (gamma Cas): RA 00h 56m 42.5s = 14.177°
    // Schedar (alpha Cas): RA 00h 40m 30.4s = 10.127°
    const rawAttempts: AlignmentAttempt[] = [
      { status: 'aligned', targetName: 'Navi', ra: 14.177, dec: 60.717, deltaRaArcsec: 0.2, deltaDecArcsec: -0.1, timestamp: 100 },
      { status: 'aligned', targetName: 'Navi', ra: 14.177, dec: 60.717, deltaRaArcsec: -0.2, deltaDecArcsec: 0.1, timestamp: 120 },
      { status: 'aligned', targetName: 'Schedar', ra: 10.127, dec: 56.537, deltaRaArcsec: 0.1, deltaDecArcsec: 0.2, timestamp: 200 },
    ];

    const clusters = clusterAlignmentAttempts(rawAttempts);
    expect(clusters).toHaveLength(2);

    const navi = clusters.find((c) => c.targetName === 'Navi');
    expect(navi).toBeDefined();
    // Must be ~14.177°, NOT 212.655° (14.177 * 15)
    expect(navi!.centroidRa).toBeCloseTo(14.177, 2);
    expect(navi!.centroidDec).toBeCloseTo(60.717, 2);

    const schedar = clusters.find((c) => c.targetName === 'Schedar');
    expect(schedar).toBeDefined();
    // Must be ~10.127°, NOT 151.905° (10.127 * 15)
    expect(schedar!.centroidRa).toBeCloseTo(10.127, 2);
    expect(schedar!.centroidDec).toBeCloseTo(56.537, 2);
  });
});
