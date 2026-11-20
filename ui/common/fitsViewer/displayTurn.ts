/**
 * @file displayTurn.ts
 * @description Shows a raw frame turned by a quarter turn (0, 90, 180 or 270 degrees) so it looks
 * the same way round as the target's stack. Frames taken on the two sides of a meridian flip are
 * upside down from each other, and this puts them back in line on screen.
 *
 * The turn is for display only. The file is never changed, and every reading (the pixel
 * position, the value, a star measurement) still names the file's own pixels. This module holds
 * the steps that decide the turn and the steps that move a point between the file and the turned
 * picture, so the drawing code, the pixel readout and the overlays all use the same rules.
 *
 * Words used here:
 * - The "base picture" is the file drawn with its first row at the top or at the bottom, as the
 *   file's `ROWORDER` says. It is not turned.
 * - The "turned picture" is the base picture turned clockwise on screen by the quarter turn.
 *   For 90 and 270 degrees its width and height are swapped.
 * - A position in either picture is counted in pixels from the picture's top-left corner.
 */
import { isPlateSolved } from './wcs';
import type { FitsHeaderCards } from './wcs';

/** A turn of the picture on screen, clockwise, in degrees. */
export type QuarterTurn = 0 | 90 | 180 | 270;

/** What the viewer needs to know about the target's stack to turn a frame the same way round. */
export interface StackOrientation {
  /** Where the stack's top points, in degrees from north toward east (see `PlateGeometry.upAngleDegrees`). */
  upAngleDegrees: number;
  /** The side of the pier the stack header names (`PIERSIDE`), or null when it has none. */
  pierSide: string | null;
}

/** A point, in pixels from the top-left corner of a picture. */
export interface PicturePoint {
  x: number;
  y: number;
}

/** One pixel of the file, named by its column and its row counted from the first row. */
export interface FileCellPosition {
  /** Column, counted from 0 at the left. */
  column: number;
  /** Row, counted from 0 at the file's first row. */
  fileRow: number;
}

const DEGREES_PER_QUARTER = 90;
const DEGREES_PER_CIRCLE = 360;

/**
 * Rounds an angle to the nearest quarter turn.
 *
 * @param degrees Any angle in degrees.
 * @returns 0, 90, 180 or 270.
 */
export function nearestQuarterTurn(degrees: number): QuarterTurn {
  const quarters = Math.round(degrees / DEGREES_PER_QUARTER);
  return ((((quarters % 4) + 4) % 4) * DEGREES_PER_QUARTER) as QuarterTurn;
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
 * Works out where north points on screen for a raw frame, from its capture rotation.
 *
 * `CROTA2` is the angle from the file's up direction (the +y axis) to north, going clockwise on
 * a picture drawn with row 1 at the bottom. A picture stored top-down is drawn upside down from
 * that, so its angle is measured from the other end of the axis.
 *
 * @param header The frame's header cards.
 * @returns Degrees clockwise from the top of the screen to north (0 up to 360), or null when the header has no `CROTA2`.
 */
export function frameNorthAngleDegrees(header: FitsHeaderCards): number | null {
  const rotation = numberOrNull(header, 'CROTA2');
  if (rotation === null) return null;
  const isTopDown = String(header['ROWORDER'] ?? '').toUpperCase() === 'TOP-DOWN';
  const angle = isTopDown ? 180 - rotation : rotation;
  return ((angle % DEGREES_PER_CIRCLE) + DEGREES_PER_CIRCLE) % DEGREES_PER_CIRCLE;
}

/**
 * Names the side of the pier a header gives, as `EAST`, `WEST` or null.
 *
 * @param header The header cards.
 * @returns The side in upper case, or null when the card is missing or says something else.
 */
function pierSideOf(header: FitsHeaderCards): string | null {
  const side = String(header['PIERSIDE'] ?? '').trim().toUpperCase();
  return side === 'EAST' || side === 'WEST' ? side : null;
}

/**
 * Reads the pier side from a header, for the stack's orientation.
 *
 * @param header The stack's header cards.
 * @returns `EAST`, `WEST` or null.
 */
export function stackPierSide(header: FitsHeaderCards): string | null {
  return pierSideOf(header);
}

/**
 * Decides how far to turn a raw frame on screen so it matches the stack.
 *
 * The turn is the stack's rotation minus the frame's, rounded to the nearest quarter turn. When the
 * frame has no `CROTA2`, the pier side decides: a frame from the other side of the pier than the
 * stack is turned half a turn. No stack, a frame that has a plate solution of its own (it is a
 * stack, or was solved) and a frame already turned to north (`QTURN`) are shown as they are.
 *
 * @param header The frame's header cards, or undefined when it has none.
 * @param stack The stack's orientation, or null when the target has no stack with a plate solution.
 * @returns The turn, clockwise in degrees.
 */
export function turnToMatchStack(
  header: FitsHeaderCards | undefined | null,
  stack: StackOrientation | null | undefined,
): QuarterTurn {
  if (!header || !stack) return 0;
  if (header['QTURN'] !== undefined || isPlateSolved(header)) return 0;
  const frameNorth = frameNorthAngleDegrees(header);
  if (frameNorth !== null) return nearestQuarterTurn(stack.upAngleDegrees - frameNorth);
  const framePier = pierSideOf(header);
  if (framePier !== null && stack.pierSide !== null && framePier !== stack.pierSide) return 180;
  return 0;
}

/**
 * Writes the note shown while a frame is displayed turned.
 *
 * @param turn The turn in degrees.
 * @returns For example "Shown turned 180° to match the stack".
 */
export function describeTurn(turn: QuarterTurn): string {
  return `Shown turned ${turn}° to match the stack`;
}

/**
 * Gives the size of the turned picture.
 *
 * @param width Width of the file, in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @returns The width and height, swapped for a quarter or three-quarter turn.
 */
export function turnedSize(width: number, height: number, turn: number): { w: number; h: number } {
  return turn === 90 || turn === 270 ? { w: height, h: width } : { w: width, h: height };
}

/**
 * Moves a point of the base picture to the turned picture.
 *
 * @param point The point, in pixels from the base picture's top-left corner.
 * @param width Width of the file (and of the base picture), in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @returns The point in the turned picture.
 */
export function baseToTurned(point: PicturePoint, width: number, height: number, turn: number): PicturePoint {
  if (turn === 90) return { x: height - point.y, y: point.x };
  if (turn === 180) return { x: width - point.x, y: height - point.y };
  if (turn === 270) return { x: point.y, y: width - point.x };
  return point;
}

/**
 * Moves a point of the turned picture back to the base picture. The opposite of {@link baseToTurned}.
 *
 * @param point The point, in pixels from the turned picture's top-left corner.
 * @param width Width of the file, in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @returns The point in the base picture.
 */
export function turnedToBase(point: PicturePoint, width: number, height: number, turn: number): PicturePoint {
  if (turn === 90) return { x: point.y, y: height - point.x };
  if (turn === 180) return { x: width - point.x, y: height - point.y };
  if (turn === 270) return { x: width - point.y, y: point.x };
  return point;
}

/**
 * Turns a direction (a step, not a position) the same way a picture is turned.
 *
 * @param stepX Step to the right in the base picture.
 * @param stepY Step down in the base picture.
 * @param turn The turn.
 * @returns The step in the turned picture.
 */
export function turnStep(stepX: number, stepY: number, turn: number): PicturePoint {
  if (turn === 90) return { x: -stepY, y: stepX };
  if (turn === 180) return { x: -stepX, y: -stepY };
  if (turn === 270) return { x: stepY, y: -stepX };
  return { x: stepX, y: stepY };
}

/**
 * Finds where a FITS pixel position is in the turned picture.
 *
 * @param x FITS column, counted from 1 (the center of the first pixel is 1.0).
 * @param y FITS row, counted from 1.
 * @param width Width of the file, in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @param firstRowAtTop True when the base picture shows the file's first row at the top.
 * @returns The position, in pixels from the turned picture's top-left corner.
 */
export function fitsPixelToTurnedPicture(
  x: number, y: number, width: number, height: number, turn: number, firstRowAtTop: boolean,
): PicturePoint {
  const base = { x: x - 0.5, y: firstRowAtTop ? y - 0.5 : height - y + 0.5 };
  return baseToTurned(base, width, height, turn);
}

/**
 * Finds the FITS pixel position under a point of the turned picture. The opposite of
 * {@link fitsPixelToTurnedPicture}.
 *
 * @param point The point, in pixels from the turned picture's top-left corner.
 * @param width Width of the file, in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @param firstRowAtTop True when the base picture shows the file's first row at the top.
 * @returns The FITS position, counted from 1.
 */
export function turnedPictureToFitsPixel(
  point: PicturePoint, width: number, height: number, turn: number, firstRowAtTop: boolean,
): PicturePoint {
  const base = turnedToBase(point, width, height, turn);
  return { x: base.x + 0.5, y: firstRowAtTop ? base.y + 0.5 : height - base.y + 0.5 };
}

/**
 * Finds the file pixel behind one cell of the turned picture.
 *
 * @param column Column of the cell in the turned picture, counted from 0 at the left.
 * @param rowFromTop Row of the cell in the turned picture, counted from 0 at the top.
 * @param width Width of the file, in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @param firstRowAtTop True when the base picture shows the file's first row at the top.
 * @returns The file column and row (both counted from 0), or null when the cell is outside the picture.
 */
export function turnedCellToFileCell(
  column: number, rowFromTop: number, width: number, height: number, turn: number, firstRowAtTop: boolean,
): FileCellPosition | null {
  const size = turnedSize(width, height, turn);
  if (column < 0 || column >= size.w || rowFromTop < 0 || rowFromTop >= size.h) return null;
  const base = turnedToBase({ x: column + 0.5, y: rowFromTop + 0.5 }, width, height, turn);
  const baseRow = Math.floor(base.y);
  return { column: Math.floor(base.x), fileRow: firstRowAtTop ? baseRow : height - 1 - baseRow };
}

/**
 * Finds the cell of the turned picture that shows the top-left corner of the base picture. The
 * file's first row is under that cell exactly when the picture shows the first row at the top,
 * so the viewer asks the worker about it to learn the row order.
 *
 * @param width Width of the file, in pixels.
 * @param height Height of the file, in pixels.
 * @param turn The turn.
 * @returns The cell: its column and its row from the top of the turned picture.
 */
export function rowOrderProbeCell(width: number, height: number, turn: number): { column: number; rowFromTop: number } {
  if (turn === 90) return { column: height - 1, rowFromTop: 0 };
  if (turn === 180) return { column: width - 1, rowFromTop: height - 1 };
  if (turn === 270) return { column: 0, rowFromTop: width - 1 };
  return { column: 0, rowFromTop: 0 };
}

/**
 * Turns a picture of pixels (4 bytes each) by a quarter turn, for drawing without the GPU.
 *
 * @param pixels The pixels, row by row from the top-left.
 * @param width Width of the picture, in pixels.
 * @param height Height of the picture, in pixels.
 * @param turn The turn.
 * @returns The turned pixels and the turned picture's width and height.
 */
export function turnRgbaPixels(
  pixels: Uint8ClampedArray<ArrayBuffer>, width: number, height: number, turn: number,
): { pixels: Uint8ClampedArray<ArrayBuffer>; width: number; height: number } {
  if (turn !== 90 && turn !== 180 && turn !== 270) return { pixels, width, height };
  const size = turnedSize(width, height, turn);
  const turned = new Uint8ClampedArray(pixels.length);
  for (let row = 0; row < height; row++) {
    for (let column = 0; column < width; column++) {
      const target = baseToTurned({ x: column + 0.5, y: row + 0.5 }, width, height, turn);
      const targetIndex = (Math.floor(target.y) * size.w + Math.floor(target.x)) * 4;
      const sourceIndex = (row * width + column) * 4;
      turned[targetIndex] = pixels[sourceIndex];
      turned[targetIndex + 1] = pixels[sourceIndex + 1];
      turned[targetIndex + 2] = pixels[sourceIndex + 2];
      turned[targetIndex + 3] = pixels[sourceIndex + 3];
    }
  }
  return { pixels: turned, width: size.w, height: size.h };
}
