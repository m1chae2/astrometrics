/**
 * @module projectionMath
 * @fileoverview Coordinate conversion and stereographic projection utilities.
 *
 * Provides stateless mathematical calculations for converting between Equatorial (RA/Dec)
 * and Horizontal (Alt/Az) coordinates, and projecting them into 2D screen space.
 */

/**
 * Converts Equatorial coordinates (RA/Dec) to Horizontal coordinates (Alt/Az).
 * Uses observer's latitude and Local Sidereal Time (LST).
 *
 * @param {number} ra - Center Right Ascension in degrees.
 * @param {number} dec - Center Declination in degrees.
 * @param {number} lst - Local Sidereal Time in degrees.
 * @param {number} lat - Observer latitude in degrees.
 * @returns {{ alt: number; az: number }} Altitude and azimuth in degrees.
 */
export function getAltAz(ra: number, dec: number, lst: number, lat: number) {
  const haRad = ((lst - ra) * Math.PI) / 180.0;
  const decRad = (dec * Math.PI) / 180.0;
  const latRad = (lat * Math.PI) / 180.0;

  const sinAlt = Math.sin(decRad) * Math.sin(latRad) + Math.cos(decRad) * Math.cos(latRad) * Math.cos(haRad);
  const altRad = Math.asin(Math.max(-1, Math.min(1, sinAlt)));
  const altDeg = (altRad * 180.0) / Math.PI;

  // atan2-based azimuth (mirrors the hour-angle calc in getRaDec below) stays
  // numerically stable through zenith transits, where the old acos()-plus-
  // sign-flip approach would flicker between mirrored azimuths as cosAz's
  // floating-point noise crossed the acos domain edge right at transit.
  const sinAz = -Math.sin(haRad) * Math.cos(decRad);
  const cosAz = Math.sin(decRad) * Math.cos(latRad) - Math.cos(decRad) * Math.sin(latRad) * Math.cos(haRad);
  const azRad = Math.atan2(sinAz, cosAz);
  const azDeg = (azRad * 180.0 / Math.PI + 360.0) % 360.0;

  return { alt: altDeg, az: azDeg };
}

/**
 * Converts Horizontal coordinates (Alt/Az) to Equatorial coordinates (RA/Dec).
 *
 * @param {number} alt - Altitude in degrees.
 * @param {number} az - Azimuth in degrees.
 * @param {number} lst - Local Sidereal Time in degrees.
 * @param {number} lat - Observer latitude in degrees.
 * @returns {{ ra: number; dec: number }} Right Ascension and Declination in degrees.
 */
export function getRaDec(alt: number, az: number, lst: number, lat: number) {
  const altRad = (alt * Math.PI) / 180.0;
  const azRad = (az * Math.PI) / 180.0;
  const latRad = (lat * Math.PI) / 180.0;

  const sinDec = Math.sin(altRad) * Math.sin(latRad) + Math.cos(altRad) * Math.cos(latRad) * Math.cos(azRad);
  const decRad = Math.asin(Math.max(-1, Math.min(1, sinDec)));
  const decDeg = (decRad * 180.0) / Math.PI;

  const cosDec = Math.cos(decRad);
  let haDeg = 0;
  if (Math.abs(cosDec) > 0.0001) {
    const sinHA = (-Math.sin(azRad) * Math.cos(altRad)) / cosDec;
    const cosHA = (Math.sin(altRad) - Math.sin(decRad) * Math.sin(latRad)) / (cosDec * Math.cos(latRad));
    const haRad = Math.atan2(sinHA, cosHA);
    haDeg = (haRad * 180.0) / Math.PI;
  }

  const raDeg = (lst - haDeg + 360.0) % 360.0;
  return { ra: raDeg, dec: decDeg };
}

/**
 * Computes the pixels-per-degree scale factor used to convert angular sizes
 * into screen-space pixels for the current viewport.
 *
 * @param {number} width - Viewport canvas width in pixels.
 * @param {number} height - Viewport canvas height in pixels.
 * @param {number} fov - Field of view in degrees.
 * @returns {number} Pixels per degree.
 */
export function pixelsPerDegree(width: number, height: number, fov: number): number {
  return Math.min(width, height) / fov;
}

/**
 * Projects Alt/Az coordinates into 2D screen space using stereographic projection.
 *
 * @param {number} alt - Target altitude in degrees.
 * @param {number} az - Target azimuth in degrees.
 * @param {number} centerAlt - Viewport center altitude in degrees.
 * @param {number} centerAz - Viewport center azimuth in degrees.
 * @param {number} fov - Field of view in degrees.
 * @param {number} width - Viewport canvas width in pixels.
 * @param {number} height - Viewport canvas height in pixels.
 * @returns {{ x: number; y: number; visible: boolean }} Screen coordinates and boundary visibility flag.
 */
export function projectAltAz(
  alt: number,
  az: number,
  centerAlt: number,
  centerAz: number,
  fov: number,
  width: number,
  height: number
) {
  let deltaAzimuth = az - centerAz;
  if (deltaAzimuth > 180) deltaAzimuth -= 360;
  if (deltaAzimuth < -180) deltaAzimuth += 360;

  const scale = pixelsPerDegree(width, height, fov);

  const altRad = (alt * Math.PI) / 180.0;
  const alt0Rad = (centerAlt * Math.PI) / 180.0;
  const deltaAzimuthRad = (deltaAzimuth * Math.PI) / 180.0;

  // Angular separation cos(A) in Alt/Az space
  const cosA = Math.sin(altRad) * Math.sin(alt0Rad) + Math.cos(altRad) * Math.cos(alt0Rad) * Math.cos(deltaAzimuthRad);

  const centerX = width / 2;
  const centerY = height / 2;

  // Stereographic projection formulas
  const denom = 1 + cosA;
  const radToDeg = 180.0 / Math.PI;
  const x = centerX + scale * radToDeg * (2 * Math.cos(altRad) * Math.sin(deltaAzimuthRad)) / denom;
  const y = centerY - scale * radToDeg * (2 * (Math.sin(altRad) * Math.cos(alt0Rad) - Math.cos(altRad) * Math.sin(alt0Rad) * Math.cos(deltaAzimuthRad))) / denom;

  const margin = 100;
  const inBounds = x >= -margin && x <= width + margin && y >= -margin && y <= height + margin;
  const visible = cosA > -0.1 && inBounds;

  return { x, y, visible };
}
