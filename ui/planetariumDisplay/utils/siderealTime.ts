/**
 * @module siderealTime
 * @fileoverview Local Sidereal Time for the planetarium's drawing loop.
 *
 * Sidereal time tells which part of the sky is overhead. The planetarium needs it
 * 60 times a second, so it uses this short formula instead of asking the backend.
 * `ui/tests/test_projectionReference.test.ts` checks it against the Python library
 * (`wayfindinglib/astronomy/coordinate_transforms.py`) at fixed times.
 */

/** Julian Date of the J2000 reference moment, 2000-01-01 12:00 UTC. */
const J2000_JULIAN_DATE = 2451545.0;

/** Julian Date of the Unix epoch, 1970-01-01 00:00 UTC. */
const UNIX_EPOCH_JULIAN_DATE = 2440587.5;

/** Milliseconds in one day. */
const MILLISECONDS_PER_DAY = 86400000.0;

/**
 * Calculates the Local Sidereal Time (LST) for a moment and a longitude.
 *
 * Uses the mean sidereal time formula from the US Naval Observatory (based on the
 * IAU 1982 standard). It leaves out the small wobble of the Earth's axis
 * (nutation), so it differs from the library's apparent sidereal time by at most
 * about 0.005 degrees (just over one second of time).
 *
 * @param {Date} simDate - The base moment (the clock, or the simulated time).
 * @param {number} offsetMinutes - Minutes to add to `simDate` (0 = no offset).
 * @param {number} lon - Observer longitude in degrees (negative = west).
 * @returns {number} Local Sidereal Time in degrees, from 0 up to 360.
 */
export const calculateLST = (simDate: Date, offsetMinutes: number, lon: number): number => {
  const date = new Date(simDate.getTime() + offsetMinutes * 60 * 1000);

  const currentJd = date.getTime() / MILLISECONDS_PER_DAY + UNIX_EPOCH_JULIAN_DATE;
  const d = currentJd - J2000_JULIAN_DATE;
  let gmst = 18.697374558 + 24.06570982441908 * d;
  gmst = (gmst % 24.0 + 24.0) % 24.0;

  return (gmst * 15.0 + lon + 360.0) % 360.0;
};
