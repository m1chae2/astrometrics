/**
 * @file test_plateGeometry.test.ts
 * @description Tests of the plate scale, field of view and rotation read from a plate solution.
 */

import { describe, expect, it } from 'vitest';
import { formatPlateScale, formatRotation, headerEntriesToCards, measurePlateGeometry } from '../plateGeometry';

/** One arcsecond-scale step of 2 arcseconds per pixel, in degrees. */
const STEP = 2 / 3600;

/** Header entries of the real M 31 ASI 533MM stack, as the backend sends them (text values). */
const M31_ASI_ENTRIES = [
  { key: 'NAXIS1', value: '2983' },
  { key: 'NAXIS2', value: '2983' },
  { key: 'ROWORDER', value: 'TOP-DOWN' },
  { key: 'XPIXSZ', value: '3.76' },
  { key: 'PC1_1', value: '0.000514216294499' },
  { key: 'PC1_2', value: '-0.000142867911728' },
  { key: 'PC2_1', value: '0.000142892438101' },
  { key: 'PC2_2', value: '0.000514095720772' },
  { key: 'CDELT1', value: '1.0' },
  { key: 'CDELT2', value: '1.0' },
  { key: 'CTYPE1', value: 'RA---TAN-SIP' },
  { key: 'A_ORDER', value: '2' },
  { key: 'PLTSOLVD', value: 'True' },
];

describe('headerEntriesToCards', () => {
  /** Text numbers become numbers and FITS true becomes T, so the plate-solve rule applies. */
  it('turns text values into numbers and FITS logicals', () => {
    const cards = headerEntriesToCards([
      { key: 'naxis1', value: '2983' },
      { key: 'PLTSOLVD', value: 'True' },
      { key: 'ROWORDER', value: 'TOP-DOWN' },
    ]);
    expect(cards).toEqual({ NAXIS1: 2983, PLTSOLVD: 'T', ROWORDER: 'TOP-DOWN' });
  });
});

describe('measurePlateGeometry', () => {
  /** The real M 31 stack: 1.92 arcseconds per pixel, about 404 mm, top pointing south-southeast. */
  it('reads the real M 31 ASI stack', () => {
    const geometry = measurePlateGeometry(headerEntriesToCards(M31_ASI_ENTRIES));
    expect(geometry).not.toBeNull();
    expect(geometry!.arcsecondsPerPixel).toBeCloseTo(1.921, 2);
    expect(geometry!.fieldWidthDegrees).toBeCloseTo(1.592, 2);
    expect(geometry!.focalLengthMm).toBeCloseTo(403.7, 0);
    expect(geometry!.upAngleDegrees).toBeCloseTo(164.5, 0);
    expect(geometry!.mirrored).toBe(false);
    expect(formatPlateScale(geometry!)).toBe('1.92″/px · 1.59° × 1.59° · 404 mm');
    expect(formatRotation(geometry!)).toBe('164.5° (top, east of north)');
  });

  /** North up and east left, stored bottom-up: the usual sky view, rotation 0. */
  it('gives 0 degrees for a north-up image', () => {
    const geometry = measurePlateGeometry({ PLTSOLVD: 'T', CD1_1: -STEP, CD1_2: 0, CD2_1: 0, CD2_2: STEP });
    expect(geometry!.arcsecondsPerPixel).toBeCloseTo(2, 6);
    expect(geometry!.upAngleDegrees).toBeCloseTo(0, 6);
    expect(geometry!.mirrored).toBe(false);
    expect(geometry!.fieldWidthDegrees).toBeNull();
    expect(geometry!.focalLengthMm).toBeNull();
  });

  /** The same pixels drawn first row at the top put south at the top, still not mirrored. */
  it('follows a top-down row order', () => {
    const geometry = measurePlateGeometry({
      PLTSOLVD: 'T', ROWORDER: 'TOP-DOWN', CD1_1: -STEP, CD1_2: 0, CD2_1: 0, CD2_2: -STEP,
    });
    expect(geometry!.upAngleDegrees).toBeCloseTo(0, 6);
    expect(geometry!.mirrored).toBe(false);
  });

  /** East on the right is a mirror image of the sky. */
  it('marks a mirror image', () => {
    const geometry = measurePlateGeometry({ PLTSOLVD: 'T', CD1_1: STEP, CD1_2: 0, CD2_1: 0, CD2_2: STEP });
    expect(geometry!.mirrored).toBe(true);
    expect(formatRotation(geometry!)).toBe('0.0° (top, east of north), mirrored');
  });

  /** A rotated image: the top points east, 90 degrees. */
  it('measures a quarter turn', () => {
    const geometry = measurePlateGeometry({ PLTSOLVD: 'T', CD1_1: 0, CD1_2: STEP, CD2_1: STEP, CD2_2: 0 });
    expect(geometry!.upAngleDegrees).toBeCloseTo(90, 6);
    expect(geometry!.mirrored).toBe(false);
  });

  /** Pointing cards from the capture program are not a plate solution. */
  it('gives nothing without a plate solution', () => {
    expect(measurePlateGeometry({ CD1_1: -STEP, CD2_2: STEP })).toBeNull();
  });
});
