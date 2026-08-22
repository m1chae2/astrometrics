/**
 * @module coordinateUtils
 * @fileoverview Shared coordinate parsing and formatting utilities for the Planetarium display.
 *
 * Consolidates safeParse, formatRA, formatDec, and formatNumber in a single location
 * so that PlanetariumDisplay, CelestialSkyMap, and PlanetariumInfoCard all use the
 * same implementation.
 *
 * REQ: PLN-1.1, REQ: PLN-2.1, REQ: PLN-2.3
 */

/**
 * Parses a coordinate value that may be a plain number, decimal string, or
 * sexagesimal string (H M S or D M S formats) into decimal degrees.
 *
 * @param {number | string | null | undefined} value - Raw input value from the backend or user input.
 * @param {boolean} assumeRA - When true, forces RA treatment (converts parsed hours to degrees).
 * @returns {number} Parsed decimal degree value, or 0 on failure.
 */
export const safeParse = (value: any, assumeRA: boolean = false): number => {
  if (value === null || value === undefined) return 0;
  if (typeof value === 'number') return assumeRA ? value * 15.0 : value;
  const str = String(value).trim();
  if (!str) return 0;

  // Plain decimal representation
  if (/^-?\d+(\.\d+)?$/.test(str)) {
    const num = parseFloat(str);
    return assumeRA ? num * 15.0 : num;
  }

  // Sexagesimal: extract numeric components
  const parts = str.match(/-?\d+(\.\d+)?/g);
  if (!parts || parts.length === 0) return 0;

  const first = parseFloat(parts[0]);
  const min = parts.length > 1 ? parseFloat(parts[1]) : 0;
  const sec = parts.length > 2 ? parseFloat(parts[2]) : 0;

  const sign = first < 0 || Object.is(first, -0) ? -1 : 1;
  const absFirst = Math.abs(first);

  const decimalValue = absFirst + min / 60.0 + sec / 3600.0;
  const finalValue = decimalValue * sign;

  const isRA = assumeRA || /h/i.test(str) || (!/°|d/i.test(str) && str.includes(' '));
  if (isRA) {
    return finalValue * 15.0;
  }
  return finalValue;
};

/**
 * Formats a Right Ascension value in decimal degrees as a sexagesimal string (H M S).
 *
 * @param {number | string} ra - RA in decimal degrees.
 * @returns {string} Formatted string such as "12h 34m 56.7s".
 */
export const formatRA = (ra: any): string => {
  const raNum = safeParse(ra);
  const raHours = raNum / 15.0;
  const h = Math.floor(raHours);
  const m = Math.floor((raHours - h) * 60.0);
  const s = ((raHours - h - m / 60.0) * 3600.0).toFixed(1);
  return `${h}h ${m}m ${s}s`;
};

/**
 * Formats a Declination value in decimal degrees as a sexagesimal string (D M S).
 *
 * @param {number | string} dec - Dec in decimal degrees.
 * @returns {string} Formatted string such as "+45° 12' 34.5"".
 */
export const formatDec = (dec: any): string => {
  const decNum = safeParse(dec);
  const sign = decNum >= 0 ? '+' : '-';
  const absDec = Math.abs(decNum);
  const d = Math.floor(absDec);
  const m = Math.floor((absDec - d) * 60.0);
  const s = ((absDec - d - m / 60.0) * 3600.0).toFixed(1);
  return `${sign}${d}° ${m}' ${s}"`;
};

/**
 * Safely formats a numeric value to a fixed number of decimal places,
 * returning '--' when the value is absent or non-numeric.
 *
 * @param {number | string | null | undefined} value - Raw value to format.
 * @param {number} decimals - Number of decimal places. Defaults to 2.
 * @returns {string} Formatted string or '--' if the value is absent or non-numeric.
 */
export const formatNumber = (value: any, decimals: number = 2): string => {
  if (value === null || value === undefined || value === '') return '--';
  const num = typeof value === 'number' ? value : parseFloat(value);
  return isNaN(num) ? '--' : num.toFixed(decimals);
};
