/**
 * @file test_viewCarry.test.ts
 * @description Tests the math that finds the same sky position in another image: the pixel and
 * fraction conversions and the walk from a sky position back to a pixel.
 */
import { describe, expect, it } from 'vitest';
import { createPixelToSky } from '../wcs';
import { fractionAtSky, fractionToFitsPixel, fitsPixelToFraction, skyAtFraction } from '../utils/transformUtils';

const HEADER = {
  PLTSOLVD: 1, CTYPE1: 'RA---TAN', CTYPE2: 'DEC--TAN', CRPIX1: 500, CRPIX2: 400,
  CRVAL1: 359.9, CRVAL2: 60, CD1_1: -0.0005, CD1_2: 0, CD2_1: 0, CD2_2: 0.0005,
};

describe('view carry math', () => {
  /** A fraction turns into a FITS pixel and back, for both row orders. */
  it('converts fractions and FITS pixels both ways', () => {
    for (const roworder of ['TOP-DOWN', 'BOTTOM-UP']) {
      const geometry = { width: 1000, height: 800, header: { ...HEADER, ROWORDER: roworder } };
      const back = fitsPixelToFraction(fractionToFitsPixel({ x: 0.3, y: 0.7 }, geometry), geometry);
      expect(back.x).toBeCloseTo(0.3, 9);
      expect(back.y).toBeCloseTo(0.7, 9);
    }
  });

  /** The walk finds the point again, also across the 0/360 degree RA line, in an image with another sky map. */
  it('finds the sky position in an image with a shifted sky map', () => {
    const first = { width: 1000, height: 800, header: HEADER };
    const second = { width: 1200, height: 900, header: { ...HEADER, CRPIX1: 650, CRPIX2: 480 } };
    const sky = skyAtFraction({ x: 0.4, y: 0.6 }, first);
    expect(sky).not.toBeNull();
    const found = fractionAtSky(sky!, second);
    expect(found).not.toBeNull();
    const pixel = fractionToFitsPixel(found!, second);
    const skyBack = createPixelToSky(second.header)!(pixel.x, pixel.y);
    expect(Math.abs(skyBack.decDegrees - sky!.decDegrees)).toBeLessThan(1e-6);
    expect(Math.abs(((skyBack.raDegrees - sky!.raDegrees + 540) % 360) - 180)).toBeLessThan(1e-6);
  });

  /** A sky position outside the image, or an image with no plate solution, gives no match. */
  it('gives no match outside the image or without a plate solution', () => {
    const image = { width: 1000, height: 800, header: HEADER };
    expect(fractionAtSky({ raDegrees: 10, decDegrees: 20 }, image)).toBeNull();
    expect(fractionAtSky({ raDegrees: 359.9, decDegrees: 60 }, { width: 1000, height: 800 })).toBeNull();
  });
});
