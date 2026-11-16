/**
 * @file plateGeometry.ts
 * @description Reads an image's plate scale, field of view and rotation from its plate solution.
 *
 * The plate solution's matrix says how far one pixel step moves on the sky, to the east and to
 * the north. Its size gives the plate scale (arcseconds per pixel). The direction of the image's
 * "up" gives the rotation, written as an angle from north toward east (a position angle).
 * With the camera's pixel size, the plate scale also gives the focal length the telescope
 * really has.
 */

import { isPlateSolved, readLinearMatrix } from './wcs';
import type { FitsHeaderCards } from './wcs';

const RADIANS_TO_DEGREES = 180 / Math.PI;
const ARCSECONDS_PER_DEGREE = 3600;
// Arcseconds in one radian (206264.8) divided by 1000 micrometers per millimeter: the plate
// scale in arcseconds per pixel equals this times the pixel size in micrometers over the focal
// length in millimeters.
const ARCSECONDS_PER_RADIAN_IN_MILLIMETERS = 206.2648;

/** What the plate solution says about an image's scale and orientation. */
export interface PlateGeometry {
  /** Sky size of one pixel, in arcseconds. */
  arcsecondsPerPixel: number;
  /** Width of the whole image on the sky, in degrees. Null when the header has no NAXIS1. */
  fieldWidthDegrees: number | null;
  /** Height of the whole image on the sky, in degrees. Null when the header has no NAXIS2. */
  fieldHeightDegrees: number | null;
  /** Where the image's top points, in degrees from north toward east (0 up to 360). */
  upAngleDegrees: number;
  /** True when the image is a mirror image of the sky (east and west swapped). */
  mirrored: boolean;
  /** Focal length worked out from the plate scale and the pixel size (XPIXSZ), in mm. */
  focalLengthMm: number | null;
}

/**
 * Reads a header card as a number.
 *
 * @param header The header cards.
 * @param key The card name.
 * @returns The number, or null when the card is missing or is not a number.
 */
function numberOrNull(header: FitsHeaderCards, key: string): number | null {
  const value = header[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Turns the header entries the backend sends (every value as text) into header cards.
 *
 * Numbers become numbers, and FITS true and false (`T`, `True`, `F`, `False`) become `T` and `F`,
 * so the same rules apply as in the viewer.
 *
 * @param entries The header entries, each with a key and a value.
 * @returns The cards, keyed by upper-case name.
 */
export function headerEntriesToCards(
  entries: ReadonlyArray<{ key: string; value?: unknown }>,
): FitsHeaderCards {
  const cards: Record<string, string | number> = {};
  for (const entry of entries) {
    if (entry.value === null || entry.value === undefined) continue;
    const text = String(entry.value).trim();
    const key = entry.key.toUpperCase();
    if (/^(t|true)$/i.test(text)) cards[key] = 'T';
    else if (/^(f|false)$/i.test(text)) cards[key] = 'F';
    else if (text !== '' && Number.isFinite(Number(text))) cards[key] = Number(text);
    else cards[key] = text;
  }
  return cards;
}

/**
 * Works out the plate scale, field of view and rotation of an image from its plate solution.
 *
 * The image's top is taken as the viewer draws it: the last row for an image stored bottom-up
 * (the FITS default), the first row for one stored with `ROWORDER = TOP-DOWN`.
 *
 * @param header The header cards.
 * @returns The geometry, or null when the image has no plate solution.
 */
export function measurePlateGeometry(header: FitsHeaderCards): PlateGeometry | null {
  if (!isPlateSolved(header)) return null;
  const matrix = readLinearMatrix(header);
  if (matrix === null) return null;
  const [eastPerColumn, eastPerRow, northPerColumn, northPerRow] = matrix;

  const determinant = eastPerColumn * northPerRow - eastPerRow * northPerColumn;
  const degreesPerPixel = Math.sqrt(Math.abs(determinant));
  const arcsecondsPerPixel = degreesPerPixel * ARCSECONDS_PER_DEGREE;

  // One step toward the top of the screen, and one step to the right, in file pixels.
  const isTopDown = String(header['ROWORDER'] ?? '').toUpperCase() === 'TOP-DOWN';
  const upRowStep = isTopDown ? -1 : 1;
  const upEast = eastPerRow * upRowStep;
  const upNorth = northPerRow * upRowStep;
  const upAngleDegrees = (Math.atan2(upEast, upNorth) * RADIANS_TO_DEGREES + 360) % 360;
  const rightAngleDegrees = (Math.atan2(eastPerColumn, northPerColumn) * RADIANS_TO_DEGREES + 360) % 360;
  // On the sky seen from below, east is 90 degrees counterclockwise of north, so with up toward
  // north, right is west (270 degrees). A mirror image puts east on the right instead.
  const turnFromUpToRight = (rightAngleDegrees - upAngleDegrees + 360) % 360;
  const mirrored = Math.abs(turnFromUpToRight - 90) < 45;

  const width = numberOrNull(header, 'NAXIS1');
  const height = numberOrNull(header, 'NAXIS2');
  const pixelSizeMicrons = numberOrNull(header, 'XPIXSZ');
  return {
    arcsecondsPerPixel,
    fieldWidthDegrees: width !== null ? width * degreesPerPixel : null,
    fieldHeightDegrees: height !== null ? height * degreesPerPixel : null,
    upAngleDegrees,
    mirrored,
    focalLengthMm: pixelSizeMicrons !== null && pixelSizeMicrons > 0
      ? (ARCSECONDS_PER_RADIAN_IN_MILLIMETERS * pixelSizeMicrons) / arcsecondsPerPixel
      : null,
  };
}

/**
 * Writes the plate scale with the field of view and the focal length, for example
 * `1.92″/px · 1.59° × 1.59° · 404 mm`.
 *
 * @param geometry The plate geometry.
 * @returns The text.
 */
export function formatPlateScale(geometry: PlateGeometry): string {
  const parts = [`${geometry.arcsecondsPerPixel.toFixed(2)}″/px`];
  if (geometry.fieldWidthDegrees !== null && geometry.fieldHeightDegrees !== null) {
    parts.push(`${geometry.fieldWidthDegrees.toFixed(2)}° × ${geometry.fieldHeightDegrees.toFixed(2)}°`);
  }
  if (geometry.focalLengthMm !== null) parts.push(`${Math.round(geometry.focalLengthMm)} mm`);
  return parts.join(' · ');
}

/**
 * Writes the rotation, for example `164.5° (top, east of north)`, noting a mirror image.
 *
 * @param geometry The plate geometry.
 * @returns The text.
 */
export function formatRotation(geometry: PlateGeometry): string {
  const text = `${geometry.upAngleDegrees.toFixed(1)}° (top, east of north)`;
  return geometry.mirrored ? `${text}, mirrored` : text;
}
