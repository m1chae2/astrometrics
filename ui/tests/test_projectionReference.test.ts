/**
 * @fileoverview Checks the planetarium's fast drawing math against the Python library.
 *
 * The planetarium works out sidereal time and altitude/azimuth in TypeScript so it can
 * redraw 60 times a second. These tests compare that math with reference numbers the
 * library (`wayfindinglib/astronomy/coordinate_transforms.py`) produced at fixed times.
 * The numbers live in `fixtures/projection_reference.json`, written by
 * `python -m wayfindinglib.scripts.generate_projection_reference`. A Python test checks
 * that the library still produces the same file.
 */

import { describe, it, expect } from 'vitest';
import reference from './fixtures/projection_reference.json';
import { getAltAz, getRaDec } from '../planetariumDisplay/utils/projectionMath';
import { calculateLST } from '../planetariumDisplay/utils/siderealTime';

/**
 * Largest allowed sidereal time difference, in degrees (36 arcseconds, 2.4 seconds of
 * time). The app's formula is mean sidereal time; the library's is apparent sidereal
 * time, which adds the nutation term (at most about 0.005 degrees). The largest
 * difference in the fixture is 0.0022 degrees.
 */
const SIDEREAL_TIME_TOLERANCE_DEG = 0.01;

/**
 * Largest allowed sky distance between the app's altitude/azimuth and the library's,
 * in degrees (36 arcseconds), when both start from the current-epoch position. Nearly
 * all of the difference comes from the sidereal time; the rest is the library's small
 * extra corrections (polar motion, aberration from the Earth's spin). The largest
 * difference in the fixture is 0.0022 degrees.
 */
const ALT_AZ_TOLERANCE_DEG = 0.01;

/**
 * Bound on the error from feeding J2000 catalog positions straight into `getAltAz`,
 * as the planetarium does, in degrees. Precession moves the frame about 0.014 degrees
 * a year (0.37 degrees by the end of 2026), so this holds through about 2035.
 */
const J2000_SHORTCUT_BOUND_DEG = 0.5;

/**
 * Returns the angle between two altitude/azimuth directions, in degrees.
 *
 * @param {number} alt1 - First altitude in degrees.
 * @param {number} az1 - First azimuth in degrees.
 * @param {number} alt2 - Second altitude in degrees.
 * @param {number} az2 - Second azimuth in degrees.
 * @returns {number} The angle between them, in degrees.
 */
function skyDistanceDeg(alt1: number, az1: number, alt2: number, az2: number): number {
  const toRad = Math.PI / 180;
  const cosAngle =
    Math.sin(alt1 * toRad) * Math.sin(alt2 * toRad) +
    Math.cos(alt1 * toRad) * Math.cos(alt2 * toRad) * Math.cos((az1 - az2) * toRad);
  return (Math.acos(Math.max(-1, Math.min(1, cosAngle))) * 180) / Math.PI;
}

/**
 * Returns the signed difference between two angles, wrapped to -180 to 180 degrees.
 *
 * @param {number} a - First angle in degrees.
 * @param {number} b - Second angle in degrees.
 * @returns {number} `a - b`, wrapped.
 */
function wrappedDifferenceDeg(a: number, b: number): number {
  return ((a - b + 540) % 360) - 180;
}

const { latitudeDeg, longitudeDeg } = reference.site;

describe('Planetarium math against the library reference', () => {
  it.each(reference.cases)('local sidereal time matches at $utc', (referenceCase) => {
    /**
     * ### Description
     * calculateLST at a fixed moment agrees with the library's apparent local
     * sidereal time within SIDEREAL_TIME_TOLERANCE_DEG.
     */
    const lst = calculateLST(new Date(referenceCase.unixMs), 0, longitudeDeg);
    expect(Math.abs(wrappedDifferenceDeg(lst, referenceCase.localSiderealTimeDeg))).toBeLessThan(
      SIDEREAL_TIME_TOLERANCE_DEG,
    );
  });

  it.each(reference.cases)('altitude and azimuth match the library at $utc', (referenceCase) => {
    /**
     * ### Description
     * getAltAz, given each star's current-epoch position and the app's own sidereal
     * time, lands within ALT_AZ_TOLERANCE_DEG of the library's altitude/azimuth.
     */
    const lst = calculateLST(new Date(referenceCase.unixMs), 0, longitudeDeg);
    for (const star of referenceCase.stars) {
      const { alt, az } = getAltAz(star.raCurrentEpochDeg, star.decCurrentEpochDeg, lst, latitudeDeg);
      expect(skyDistanceDeg(alt, az, star.altDeg, star.azDeg), star.name).toBeLessThan(ALT_AZ_TOLERANCE_DEG);
    }
  });

  it.each(reference.cases)('getRaDec turns the library altitude/azimuth back into RA/Dec at $utc', (referenceCase) => {
    /**
     * ### Description
     * getRaDec, the inverse used for mouse clicks, recovers each star's
     * current-epoch position from the library's altitude/azimuth.
     */
    const lst = calculateLST(new Date(referenceCase.unixMs), 0, longitudeDeg);
    for (const star of referenceCase.stars) {
      const { ra, dec } = getRaDec(star.altDeg, star.azDeg, lst, latitudeDeg);
      const raOnSkyDeg = wrappedDifferenceDeg(ra, star.raCurrentEpochDeg) * Math.cos((dec * Math.PI) / 180);
      expect(Math.hypot(raOnSkyDeg, dec - star.decCurrentEpochDeg), star.name).toBeLessThan(ALT_AZ_TOLERANCE_DEG);
    }
  });

  it.each(reference.cases)('drawing J2000 positions directly stays within the stated bound at $utc', (referenceCase) => {
    /**
     * ### Description
     * The planetarium draws catalog stars from their J2000 positions without
     * precessing them. Every object it draws (stars, targets, the mount) uses the
     * same frame, so they line up with each other; only their place against the
     * horizon is off, by less than J2000_SHORTCUT_BOUND_DEG.
     */
    const lst = calculateLST(new Date(referenceCase.unixMs), 0, longitudeDeg);
    for (const star of referenceCase.stars) {
      const { alt, az } = getAltAz(star.raJ2000Deg, star.decJ2000Deg, lst, latitudeDeg);
      expect(skyDistanceDeg(alt, az, star.altDeg, star.azDeg), star.name).toBeLessThan(J2000_SHORTCUT_BOUND_DEG);
    }
  });
});
