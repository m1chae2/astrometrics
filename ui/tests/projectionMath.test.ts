/**
 * @fileoverview Unit test suite for coordinate conversions and stereographic projections.
 *
 * Verifies correctness of Alt/Az conversion to and from RA/Dec, and validates
 * that 3D celestial coordinates map correctly to 2D screen positions.
 */

import { describe, it, expect } from 'vitest';
import { getAltAz, getRaDec, projectAltAz, pixelsPerDegree } from '../planetariumDisplay/utils/projectionMath';

describe('Coordinate Projections Math Suite', () => {

  it('should convert RA/Dec to Alt/Az and match reverse conversion', () => {
    /**
     * ### Description
     * Asserts that getAltAz and getRaDec are mathematical inverses of each other
     * for a given latitude and local sidereal time.
     */
    const ra = 150.0;
    const dec = 45.0;
    const lst = 280.0;
    const lat = 39.7;

    const altAz = getAltAz(ra, dec, lst, lat);
    expect(altAz.alt).toBeLessThanOrEqual(90);
    expect(altAz.alt).toBeGreaterThanOrEqual(-90);
    expect(altAz.az).toBeLessThanOrEqual(360);
    expect(altAz.az).toBeGreaterThanOrEqual(0);

    const raDec = getRaDec(altAz.alt, altAz.az, lst, lat);
    expect(raDec.ra).toBeCloseTo(ra, 4);
    expect(raDec.dec).toBeCloseTo(dec, 4);
  });

  it('should project zenith directly to center of the canvas', () => {
    /**
     * ### Description
     * Asserts that pointing coordinates located exactly at the center of the camera
     * viewport map exactly to the centerX and centerY of the screen space.
     */
    const width = 800;
    const height = 600;
    const centerAlt = 45.0;
    const centerAz = 180.0;
    const fov = 2.0;

    const projection = projectAltAz(centerAlt, centerAz, centerAlt, centerAz, fov, width, height);
    expect(projection.visible).toBe(true);
    expect(projection.x).toBeCloseTo(width / 2, 4);
    expect(projection.y).toBeCloseTo(height / 2, 4);
  });

  it('should mark coordinates outside FOV margin as invisible', () => {
    /**
     * ### Description
     * Asserts that coordinates far away from the viewport center are marked as invisible.
     */
    const width = 800;
    const height = 600;
    const centerAlt = 45.0;
    const centerAz = 180.0;
    const fov = 1.0;

    // Alt differs by 90 degrees, far outside the 1.0 degree FOV
    const projection = projectAltAz(-45.0, centerAz, centerAlt, centerAz, fov, width, height);
    expect(projection.visible).toBe(false);
  });

  it('should project off-center coordinates to correct pixel distances based on FOV scale', () => {
    /**
     * ### Description
     * Asserts that a coordinate shifted by exactly 1.0 degree in altitude
     * projects to the correct number of pixels away from the center (scale = width / fov).
     */
    const width = 800;
    const height = 600;
    const centerAlt = 45.0;
    const centerAz = 180.0;
    const fov = 2.0; // scale = 600 px / 2 deg = 300 px/deg

    // Shift by 1.0 degree in altitude
    const projection = projectAltAz(46.0, centerAz, centerAlt, centerAz, fov, width, height);
    expect(projection.visible).toBe(true);
    expect(projection.x).toBeCloseTo(width / 2, 2);
    // Expected distance is 2 * tan(0.5 deg) * (180 / PI) * scale
    // 2 * tan(0.5 deg) * (180 / PI) * 300 = 1.000025 * 300 = 300 px (approx)
    expect(projection.y).toBeCloseTo(height / 2 - 300, 1);
  });

  it('should compute pixels-per-degree as the shorter viewport dimension over FOV', () => {
    /**
     * ### Description
     * Asserts pixelsPerDegree matches the scale factor projectAltAz derives
     * internally, since overlay layers rely on both staying in sync.
     */
    expect(pixelsPerDegree(800, 600, 2.0)).toBeCloseTo(300, 6);
    expect(pixelsPerDegree(600, 800, 2.0)).toBeCloseTo(300, 6);
    expect(pixelsPerDegree(1920, 1080, 90)).toBeCloseTo(1080 / 90, 6);
  });
});
