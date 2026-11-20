/**
 * @file wcs.ts
 * @description Turns a pixel position into a sky position (RA and Dec) from a FITS header, for the viewer's pixel readout.
 *
 * Supported: the gnomonic (TAN) projection, written `RA---TAN` and `DEC--TAN`, with the
 * linear part given as a CD matrix or as a PC matrix with CDELT, and the optional SIP
 * polynomial that corrects lens distortion (`A_p_q` and `B_p_q` cards). Anything else gives no
 * sky position.
 *
 * Only a plate solution counts. A camera program writes the place the mount was told to point,
 * and a position worked out from that can be many pixels off. This file follows the library's
 * rule (`header_is_plate_solved` in `astrometricslib`): the header says `PLTSOLVD = T`, or it
 * has SIP terms (`A_ORDER`), which a solver fits and a pointing guess does not have.
 *
 * Pixel numbers here are FITS pixel numbers: counted from 1, with the center of the first pixel at 1.0.
 */

/** FITS header cards the viewer read: text cards as text, numbers as numbers, keyed by upper-case name. */
export type FitsHeaderCards = Readonly<Record<string, string | number>>;

/** A position on the sky, in degrees. */
export interface SkyPosition {
  /** Right ascension, from 0 up to 360 degrees. */
  raDegrees: number;
  /** Declination, from -90 to +90 degrees. */
  decDegrees: number;
}

/** Turns a FITS pixel position (x, y, counted from 1) into a sky position. */
export type PixelToSky = (x: number, y: number) => SkyPosition;

const DEGREES_TO_RADIANS = Math.PI / 180;
const RADIANS_TO_DEGREES = 180 / Math.PI;

/** One SIP polynomial term: the coefficient times u^uPower times v^vPower. */
interface SipTerm {
  uPower: number;
  vPower: number;
  coefficient: number;
}

/**
 * Reads a header card as a number.
 *
 * @param header The header cards.
 * @param key The card name.
 * @returns The number, or undefined when the card is missing or is not a number.
 */
function numberCard(header: FitsHeaderCards, key: string): number | undefined {
  const value = header[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/**
 * Tells whether a header's sky map came from a plate solve, by the library's rule.
 *
 * @param header The header cards.
 * @returns True when `PLTSOLVD` is true, or the header has SIP terms (`A_ORDER`).
 */
export function isPlateSolved(header: FitsHeaderCards): boolean {
  const solvedCard = header['PLTSOLVD'];
  const isMarkedSolved = solvedCard === 'T' || solvedCard === 'TRUE' || (typeof solvedCard === 'number' && solvedCard !== 0);
  return isMarkedSolved || header['A_ORDER'] !== undefined;
}

/**
 * Collects the SIP terms of one axis (`A` for x, `B` for y).
 *
 * @param header The header cards.
 * @param prefix `A` or `B`.
 * @returns The terms, empty when the axis has no SIP order.
 */
function readSipTerms(header: FitsHeaderCards, prefix: 'A' | 'B'): SipTerm[] {
  const order = numberCard(header, `${prefix}_ORDER`);
  if (order === undefined) return [];
  const terms: SipTerm[] = [];
  for (let uPower = 0; uPower <= order; uPower++) {
    for (let vPower = 0; uPower + vPower <= order; vPower++) {
      const coefficient = numberCard(header, `${prefix}_${uPower}_${vPower}`);
      if (coefficient !== undefined && coefficient !== 0) terms.push({ uPower, vPower, coefficient });
    }
  }
  return terms;
}

/**
 * Adds up SIP terms at one offset from the reference pixel.
 *
 * @param terms The polynomial terms.
 * @param u The offset in x, in pixels.
 * @param v The offset in y, in pixels.
 * @returns The correction to add to the offset.
 */
function evaluateSip(terms: readonly SipTerm[], u: number, v: number): number {
  let total = 0;
  for (const term of terms) total += term.coefficient * Math.pow(u, term.uPower) * Math.pow(v, term.vPower);
  return total;
}

/**
 * Reads the linear part of a sky map: the 2 by 2 matrix from pixel offsets to degrees on the
 * tangent plane, from the CD cards or from PC times CDELT.
 *
 * @param header The header cards.
 * @returns The matrix as [row 1 column 1, row 1 column 2, row 2 column 1, row 2 column 2], or
 *   null when every entry is zero. The first row gives the step to the east, the second the
 *   step to the north.
 */
export function readLinearMatrix(header: FitsHeaderCards): [number, number, number, number] | null {
  let matrix: [number, number, number, number];
  const cd11 = numberCard(header, 'CD1_1');
  if (cd11 !== undefined || numberCard(header, 'CD1_2') !== undefined || numberCard(header, 'CD2_1') !== undefined) {
    matrix = [cd11 ?? 0, numberCard(header, 'CD1_2') ?? 0, numberCard(header, 'CD2_1') ?? 0, numberCard(header, 'CD2_2') ?? 0];
  } else {
    const pixelScaleX = numberCard(header, 'CDELT1') ?? 1;
    const pixelScaleY = numberCard(header, 'CDELT2') ?? 1;
    const pc11 = numberCard(header, 'PC1_1') ?? 1;
    const pc12 = numberCard(header, 'PC1_2') ?? 0;
    const pc21 = numberCard(header, 'PC2_1') ?? 0;
    const pc22 = numberCard(header, 'PC2_2') ?? 1;
    matrix = [pixelScaleX * pc11, pixelScaleX * pc12, pixelScaleY * pc21, pixelScaleY * pc22];
  }
  return matrix.every((entry) => entry === 0) ? null : matrix;
}

/**
 * Builds the function that turns pixels into sky positions, from a header with a plate solution.
 *
 * The steps follow the FITS standard: subtract the reference pixel, add the SIP correction,
 * multiply by the CD matrix (or by PC and then CDELT), then undo the gnomonic projection
 * around the reference sky position (CRVAL).
 *
 * @param header The header cards.
 * @returns The conversion, or null when the header has no plate solution or its sky map is
 *   not a TAN projection with the cards needed.
 */
export function createPixelToSky(header: FitsHeaderCards): PixelToSky | null {
  if (!isPlateSolved(header)) return null;
  const typeX = String(header['CTYPE1'] ?? '').toUpperCase();
  const typeY = String(header['CTYPE2'] ?? '').toUpperCase();
  if (!typeX.startsWith('RA---TAN') || !typeY.startsWith('DEC--TAN')) return null;

  const referencePixelX = numberCard(header, 'CRPIX1');
  const referencePixelY = numberCard(header, 'CRPIX2');
  const referenceRa = numberCard(header, 'CRVAL1');
  const referenceDec = numberCard(header, 'CRVAL2');
  if (
    referencePixelX === undefined || referencePixelY === undefined ||
    referenceRa === undefined || referenceDec === undefined
  ) return null;

  const matrix = readLinearMatrix(header);
  if (matrix === null) return null;

  const sipTermsX = readSipTerms(header, 'A');
  const sipTermsY = readSipTerms(header, 'B');
  const referenceRaRadians = referenceRa * DEGREES_TO_RADIANS;
  const sinReferenceDec = Math.sin(referenceDec * DEGREES_TO_RADIANS);
  const cosReferenceDec = Math.cos(referenceDec * DEGREES_TO_RADIANS);

  return (x: number, y: number): SkyPosition => {
    const u = x - referencePixelX;
    const v = y - referencePixelY;
    const correctedU = u + evaluateSip(sipTermsX, u, v);
    const correctedV = v + evaluateSip(sipTermsY, u, v);
    // Position on the tangent plane, in radians: xi grows to the east, eta to the north.
    const xi = (matrix[0] * correctedU + matrix[1] * correctedV) * DEGREES_TO_RADIANS;
    const eta = (matrix[2] * correctedU + matrix[3] * correctedV) * DEGREES_TO_RADIANS;
    const denominator = cosReferenceDec - eta * sinReferenceDec;
    let raDegrees = (referenceRaRadians + Math.atan2(xi, denominator)) * RADIANS_TO_DEGREES;
    raDegrees = ((raDegrees % 360) + 360) % 360;
    const decDegrees = Math.atan2(
      sinReferenceDec + eta * cosReferenceDec,
      Math.hypot(denominator, xi),
    ) * RADIANS_TO_DEGREES;
    return { raDegrees, decDegrees };
  };
}

/**
 * Writes a right ascension as hours, minutes and seconds, for example `00h 42m 44.30s`.
 *
 * @param raDegrees Right ascension in degrees.
 * @returns The text, with seconds to two decimals.
 */
export function formatRightAscension(raDegrees: number): string {
  // Count in hundredths of a second so that rounding carries into the minutes and hours.
  const wrapped = ((raDegrees % 360) + 360) % 360;
  const hundredths = Math.round((wrapped / 15) * 3600 * 100) % (24 * 3600 * 100);
  const hours = Math.floor(hundredths / (3600 * 100));
  const minutes = Math.floor((hundredths % (3600 * 100)) / (60 * 100));
  const seconds = (hundredths % (60 * 100)) / 100;
  return `${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}m ${seconds.toFixed(2).padStart(5, '0')}s`;
}

/**
 * Writes a declination as degrees, arcminutes and arcseconds, for example `+41° 16′ 09.0″`.
 *
 * @param decDegrees Declination in degrees.
 * @returns The text, with a sign and arcseconds to one decimal.
 */
export function formatDeclination(decDegrees: number): string {
  const sign = decDegrees < 0 ? '-' : '+';
  // Count in tenths of an arcsecond so that rounding carries into the arcminutes and degrees.
  const tenths = Math.round(Math.abs(decDegrees) * 3600 * 10);
  const degrees = Math.floor(tenths / (3600 * 10));
  const arcminutes = Math.floor((tenths % (3600 * 10)) / (60 * 10));
  const arcseconds = (tenths % (60 * 10)) / 10;
  return `${sign}${String(degrees).padStart(2, '0')}° ${String(arcminutes).padStart(2, '0')}′ ${arcseconds.toFixed(1).padStart(4, '0')}″`;
}
