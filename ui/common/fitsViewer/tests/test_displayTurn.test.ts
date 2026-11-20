/**
 * @file test_displayTurn.test.ts
 * @description Tests how a raw frame is turned to match its stack: the turn worked out from the header
 * (NGC 7331's two sides of the meridian flip), the steps that move a point between the file and the
 * turned picture, the file pixel the worker names under a turned picture, the Detections marker and the
 * view carry-over under a turn, and the screen direction names.
 */
import { beforeAll, describe, expect, it, vi } from 'vitest';
import {
  baseToTurned, describeTurn, fitsPixelToTurnedPicture, frameNorthAngleDegrees, nearestQuarterTurn, rowOrderProbeCell,
  turnRgbaPixels, turnStep, turnToMatchStack, turnedCellToFileCell, turnedPictureToFitsPixel, turnedSize, turnedToBase,
} from '../displayTurn';
import type { StackOrientation } from '../displayTurn';
import { pointerToFileCell } from '../utils/pixelQuery';
import { detectionScreenPosition } from '../utils/detectionMarkers';
import { fractionAtSky, fractionToFitsPixel, skyAtFraction } from '../utils/transformUtils';
import { screenDirection } from '../utils/starRing';

/** NGC 7331's stack: north at 344 degrees on screen, matching the frames from the east side of the pier. */
const STACK: StackOrientation = { upAngleDegrees: 344, pierSide: 'EAST' };

/** A raw NGC 7331 frame from the west side of the pier (frames 001 to 045). */
const FRAME_001 = { ROWORDER: 'TOP-DOWN', CROTA2: 16.2, PIERSIDE: 'WEST' };

/** A raw NGC 7331 frame from the east side of the pier (frames 046 to 135). */
const FRAME_080 = { ROWORDER: 'TOP-DOWN', CROTA2: 196.1, PIERSIDE: 'EAST' };

describe('turn to match the stack', () => {
  /** Frame 001 is half a turn from the stack. */
  it('turns the west-side frame by 180 degrees', () => {
    expect(turnToMatchStack(FRAME_001, STACK)).toBe(180);
  });

  /** Frame 080 already matches. */
  it('leaves the east-side frame as it is', () => {
    expect(turnToMatchStack(FRAME_080, STACK)).toBe(0);
  });

  /** The capture rotation is read with the row order in mind. */
  it('reads the north angle from CROTA2 and the row order', () => {
    expect(frameNorthAngleDegrees(FRAME_080)).toBeCloseTo(343.9, 5);
    expect(frameNorthAngleDegrees({ CROTA2: 30 })).toBe(30);
    expect(frameNorthAngleDegrees({ ROWORDER: 'TOP-DOWN' })).toBeNull();
  });

  /** Quarter turns come out of a rotation a quarter of a circle apart. */
  it('rounds to the nearest quarter turn', () => {
    expect(turnToMatchStack({ ROWORDER: 'TOP-DOWN', CROTA2: 196.1 - 90 }, STACK)).toBe(270);
    expect(turnToMatchStack({ ROWORDER: 'TOP-DOWN', CROTA2: 196.1 + 90 }, STACK)).toBe(90);
    expect(nearestQuarterTurn(-44)).toBe(0);
    expect(nearestQuarterTurn(-46)).toBe(270);
    expect(nearestQuarterTurn(360 + 179.7)).toBe(180);
  });

  /** Without CROTA2 the pier side decides. */
  it('falls back to the pier side when there is no CROTA2', () => {
    expect(turnToMatchStack({ ROWORDER: 'TOP-DOWN', PIERSIDE: 'WEST' }, STACK)).toBe(180);
    expect(turnToMatchStack({ ROWORDER: 'TOP-DOWN', PIERSIDE: 'EAST' }, STACK)).toBe(0);
    expect(turnToMatchStack({ ROWORDER: 'TOP-DOWN' }, STACK)).toBe(0);
    expect(turnToMatchStack({ PIERSIDE: 'WEST' }, { upAngleDegrees: 344, pierSide: null })).toBe(0);
  });

  /** No stack, no turn. A frame with its own plate solution or already turned to north is left alone. */
  it('does not turn without a stack, for a solved frame or for a frame already turned', () => {
    expect(turnToMatchStack(FRAME_001, null)).toBe(0);
    expect(turnToMatchStack(undefined, STACK)).toBe(0);
    expect(turnToMatchStack({ ...FRAME_001, PLTSOLVD: 1 }, STACK)).toBe(0);
    expect(turnToMatchStack({ ...FRAME_001, QTURN: 180 }, STACK)).toBe(0);
  });

  /** The note names the turn. */
  it('describes the turn', () => {
    expect(describeTurn(180)).toBe('Shown turned 180° to match the stack');
  });
});

describe('moving points between the file and the turned picture', () => {
  const width = 6;
  const height = 4;

  /** Turning and turning back returns every point, and a quarter turn swaps the size. */
  it('round trips for every turn and row order', () => {
    for (const turn of [0, 90, 180, 270]) {
      for (const firstRowAtTop of [true, false]) {
        const picture = fitsPixelToTurnedPicture(2, 3, width, height, turn, firstRowAtTop);
        const back = turnedPictureToFitsPixel(picture, width, height, turn, firstRowAtTop);
        expect(back.x).toBeCloseTo(2, 9);
        expect(back.y).toBeCloseTo(3, 9);
        const base = turnedToBase(baseToTurned({ x: 1.5, y: 0.5 }, width, height, turn), width, height, turn);
        expect(base.x).toBeCloseTo(1.5, 9);
        expect(base.y).toBeCloseTo(0.5, 9);
      }
    }
    expect(turnedSize(width, height, 90)).toEqual({ w: 4, h: 6 });
    expect(turnedSize(width, height, 180)).toEqual({ w: 6, h: 4 });
  });

  /** A quarter turn clockwise puts the top-left corner at the top-right. */
  it('turns clockwise', () => {
    expect(baseToTurned({ x: 0, y: 0 }, width, height, 90)).toEqual({ x: height, y: 0 });
    expect(baseToTurned({ x: 0, y: 0 }, width, height, 180)).toEqual({ x: width, y: height });
    expect(baseToTurned({ x: 0, y: 0 }, width, height, 270)).toEqual({ x: 0, y: width });
    expect(turnStep(1, 0, 90)).toEqual({ x: -0, y: 1 });
  });

  /** Under 180 degrees the cell at the top-left of the screen is the file's last column, and the last or first row. */
  it('names the file pixel under a turned picture', () => {
    expect(turnedCellToFileCell(0, 0, width, height, 180, true)).toEqual({ column: 5, fileRow: 3 });
    expect(turnedCellToFileCell(0, 0, width, height, 180, false)).toEqual({ column: 5, fileRow: 0 });
    expect(turnedCellToFileCell(5, 3, width, height, 180, true)).toEqual({ column: 0, fileRow: 0 });
    expect(turnedCellToFileCell(0, 0, width, height, 0, true)).toEqual({ column: 0, fileRow: 0 });
    expect(turnedCellToFileCell(0, 0, width, height, 90, true)).toEqual({ column: 0, fileRow: 3 });
    expect(turnedCellToFileCell(6, 0, width, height, 180, true)).toBeNull();
    expect(turnedCellToFileCell(4, 0, width, height, 90, true)).toBeNull();
  });

  /** The probe cell shows the file's top-left base corner in every turn. */
  it('finds the cell that shows the base picture top-left corner', () => {
    for (const turn of [0, 90, 180, 270]) {
      const cell = rowOrderProbeCell(width, height, turn);
      expect(turnedCellToFileCell(cell.column, cell.rowFromTop, width, height, turn, true)).toEqual({ column: 0, fileRow: 0 });
    }
  });

  /** The pointer finds the cell of the turned picture, using the turned size. */
  it('finds the drawn cell under the pointer for a quarter turn', () => {
    // A 600 x 400 file shown turned 90 degrees is 400 wide and 600 tall; drawn at half size, 200 x 300.
    const cell = pointerToFileCell(100, 150, { w: 600, h: 400 }, { w: 200, h: 300 }, 1, 0, 0, 90);
    expect(cell).toEqual({ column: 200, rowFromTop: 300 });
  });

  /** The Detections marker lands at the turned place. */
  it('places a detection under 180 degrees', () => {
    const geometry = {
      imageWidth: 200, imageHeight: 160, stepX: 1, stepY: 1, panX: 0, panY: 0,
      firstRowAtTop: true, viewWidth: 200, viewHeight: 160,
    };
    expect(detectionScreenPosition({ x: 11, y: 21 }, geometry)).toEqual({ x: 10.5, y: 20.5 });
    expect(detectionScreenPosition({ x: 11, y: 21 }, { ...geometry, turn: 180 })).toEqual({ x: 189.5, y: 139.5 });
    expect(detectionScreenPosition({ x: 11, y: 21 }, { ...geometry, firstRowAtTop: false, turn: 180 })).toEqual({ x: 189.5, y: 20.5 });
  });

  /** The view carry-over: a sky position and a relative position agree across a turn. */
  it('keeps the sky position and the pixel under the center across a turn', () => {
    const header = {
      PLTSOLVD: 1, CTYPE1: 'RA---TAN', CTYPE2: 'DEC--TAN', CRPIX1: 100, CRPIX2: 80,
      CRVAL1: 150, CRVAL2: 20, CD1_1: -0.0005, CD1_2: 0, CD2_1: 0, CD2_2: 0.0005,
    };
    const plain = { width: 200, height: 160, header };
    const turned = { ...plain, turn: 180 };
    const fraction = { x: 0.3, y: 0.2 };
    // The same star is at the mirrored fractions of the turned picture.
    const sky = skyAtFraction(fraction, plain)!;
    const found = fractionAtSky(sky, turned)!;
    expect(found.x).toBeCloseTo(0.7, 3);
    expect(found.y).toBeCloseTo(0.8, 3);
    const pixel = fractionToFitsPixel({ x: 0.7, y: 0.8 }, turned);
    expect(pixel.x).toBeCloseTo(fractionToFitsPixel(fraction, plain).x, 9);
    expect(pixel.y).toBeCloseTo(fractionToFitsPixel(fraction, plain).y, 9);
  });

  /** A turned picture of colored pixels is a re-indexing: nothing is lost. */
  it('turns drawn pixels without losing any', () => {
    const pixels = new Uint8ClampedArray(2 * 3 * 4);
    for (let index = 0; index < 6; index += 1) pixels[index * 4] = index + 1;
    const half = turnRgbaPixels(pixels, 2, 3, 180);
    expect(Array.from({ length: 6 }, (_, index) => half.pixels[index * 4])).toEqual([6, 5, 4, 3, 2, 1]);
    const quarter = turnRgbaPixels(pixels, 2, 3, 90);
    expect([quarter.width, quarter.height]).toEqual([3, 2]);
    // Row 0 of the turned picture is the original left column read from the bottom up.
    expect(Array.from({ length: 3 }, (_, index) => quarter.pixels[index * 4])).toEqual([5, 3, 1]);
  });

  /** The ring's direction names follow the turn. */
  it('names screen directions under a turn', () => {
    expect(screenDirection(0, true)).toBe('Down');
    expect(screenDirection(0, true, 180)).toBe('Up');
    expect(screenDirection(90, true, 180)).toBe('Left');
    expect(screenDirection(90, false, 90)).toBe('Down');
  });
});

describe('the worker names the file pixel under a turned picture', () => {
  const postMessage = vi.fn();
  let handle: (event: { data: Record<string, unknown> }) => Promise<void>;

  beforeAll(async () => {
    (self as unknown as { postMessage: unknown }).postMessage = postMessage;
    await import('../fitsWorker');
    handle = (self as unknown as { onmessage: typeof handle }).onmessage;
  });

  /**
   * Makes a 4 x 3 float FITS file whose pixel values are 0 to 11 in file order.
   *
   * @returns The bytes and the offset of the data.
   */
  function makeFile(): ArrayBuffer {
    const buffer = new ArrayBuffer(2880 + 12 * 4);
    const view = new DataView(buffer);
    for (let index = 0; index < 12; index += 1) view.setFloat32(2880 + index * 4, index, false);
    return buffer;
  }

  /**
   * Asks for one pixel and returns the worker's answer.
   *
   * @param column Column of the drawn picture.
   * @param rowFromTop Row of the drawn picture.
   * @returns The reading.
   */
  async function readPixel(column: number, rowFromTop: number) {
    postMessage.mockClear();
    await handle({ data: { cmd: 'pixel', requestId: 9, imageId: 1, column, rowFromTop } });
    return postMessage.mock.calls[0][0].result as { x: number; y: number; values: number[] } | null;
  }

  /** Under 180 degrees the readout still reports the file's own pixel position and value. */
  it('reports file coordinates and the file value', async () => {
    await handle({ data: {
      cmd: 'parse', requestId: 1, imageId: 1, pw: 4, ph: 3, channels: 1, roworder: 'TOP-DOWN',
      rawBuffer: makeFile(), bitpix: -32, bzero: 0, bscale: 1, dataOffset: 2880,
    } });
    // There is no GPU or bitmap here, so the draw itself fails after it has recorded how it was asked to draw.
    await handle({ data: { cmd: 'render', requestId: 2, imageId: 1, dstW: 4, dstH: 3, dpr: 1, stretch: false, turn: 180 } });
    const topLeft = await readPixel(0, 0);
    expect(topLeft).toEqual({ x: 4, y: 3, values: [11] });
    const bottomRight = await readPixel(3, 2);
    expect(bottomRight).toEqual({ x: 1, y: 1, values: [0] });
    expect(await readPixel(4, 0)).toBeNull();
    await handle({ data: { cmd: 'render', requestId: 3, imageId: 1, dstW: 4, dstH: 3, dpr: 1, stretch: false, turn: 0 } });
    expect(await readPixel(0, 0)).toEqual({ x: 1, y: 1, values: [0] });
  });
});
