/**
 * @file test_wcs.test.ts
 * @description Checks the sky-position math of the pixel readout against astropy.
 *
 * The reference numbers were made with `astropy.wcs.WCS(header).all_pix2world(x, y, 1)` on the
 * header of a real M 31 stack (TAN-SIP, order 2), so the TypeScript math must agree with the
 * library's own WCS to within 0.1 arcsecond.
 */
import { describe, expect, it } from 'vitest';
import {
  createPixelToSky,
  formatDeclination,
  formatRightAscension,
  isPlateSolved,
} from '../wcs';
import type { FitsHeaderCards } from '../wcs';

/** Header cards of the M 31 stack, with its SIP terms. */
const M31_SIP_HEADER: FitsHeaderCards = {
  CTYPE1: 'RA---TAN-SIP',
  CTYPE2: 'DEC--TAN-SIP',
  CRPIX1: 1685.93223572,
  CRPIX2: 1206.48699951,
  CRVAL1: 10.8810120906,
  CRVAL2: 41.1458844842,
  PC1_1: 0.000514216294499,
  PC1_2: -0.000142867911728,
  PC2_1: 0.000142892438101,
  PC2_2: 0.000514095720772,
  CDELT1: 1.0,
  CDELT2: 1.0,
  A_ORDER: 2,
  A_0_2: 3.86811111223e-08,
  A_1_1: -3.17082764667e-08,
  A_2_0: -8.64158479851e-08,
  B_ORDER: 2,
  B_0_2: -4.90330543689e-08,
  B_1_1: -1.98328020657e-08,
  B_2_0: -1.25641599357e-07,
};

/** The same header without SIP terms, marked as a plate solution by `PLTSOLVD`. */
const M31_NO_SIP_HEADER: FitsHeaderCards = Object.fromEntries(
  Object.entries(M31_SIP_HEADER)
    .filter(([key]) => !/^[AB]_/.test(key))
    .map(([key, value]) => [key, key.startsWith('CTYPE') ? String(value).replace('-SIP', '') : value]),
);

/**
 * Gives the angle between two sky positions, in arcseconds.
 *
 * @param first The first position in degrees.
 * @param second The second position in degrees.
 * @returns The separation in arcseconds.
 */
function separationArcseconds(
  first: { raDegrees: number; decDegrees: number },
  second: { raDegrees: number; decDegrees: number },
): number {
  const toRadians = Math.PI / 180;
  const deltaDec = (second.decDegrees - first.decDegrees) * toRadians;
  const deltaRa = (second.raDegrees - first.raDegrees) * toRadians;
  const a = Math.sin(deltaDec / 2) ** 2 +
    Math.cos(first.decDegrees * toRadians) * Math.cos(second.decDegrees * toRadians) * Math.sin(deltaRa / 2) ** 2;
  return (2 * Math.asin(Math.sqrt(a)) / toRadians) * 3600;
}

describe('createPixelToSky', () => {
  it('agrees with astropy on a TAN-SIP header to within 0.1 arcsecond', () => {
    const pixelToSky = createPixelToSky(M31_SIP_HEADER);
    expect(pixelToSky).not.toBeNull();
    const references: Array<[number, number, number, number]> = [
      [1, 1, 9.971090084557499, 40.28160856191562],
      [1685.93223572, 1206.48699951, 10.8810120906, 41.1458844842],
      [3370, 2412, 11.814353380899012, 42.00215239456375],
      [100, 2000, 9.644231889190575, 41.320400534054734],
      [3000, 300, 11.945660214334671, 40.86260987252098],
    ];
    for (const [x, y, ra, dec] of references) {
      const sky = pixelToSky!(x, y);
      expect(separationArcseconds(sky, { raDegrees: ra, decDegrees: dec })).toBeLessThan(0.1);
    }
  });

  it('uses the SIP terms: leaving them out moves the corner by more than 0.1 arcsecond', () => {
    const withSip = createPixelToSky(M31_SIP_HEADER)!(1, 1);
    const withoutSip = createPixelToSky({ ...M31_NO_SIP_HEADER, PLTSOLVD: 'T' })!(1, 1);
    expect(separationArcseconds(withSip, withoutSip)).toBeGreaterThan(0.1);
  });

  it('agrees with astropy on a header with no SIP terms, marked by PLTSOLVD', () => {
    const pixelToSky = createPixelToSky({ ...M31_NO_SIP_HEADER, PLTSOLVD: 'T' })!;
    expect(separationArcseconds(pixelToSky(1, 1), { raDegrees: 9.971169467769759, decDegrees: 40.28188606011775 })).toBeLessThan(0.1);
    expect(separationArcseconds(pixelToSky(3370, 2412), { raDegrees: 11.814442633483646, decDegrees: 42.00242831229505 })).toBeLessThan(0.1);
  });

  it('reads a CD matrix as well as PC with CDELT', () => {
    const cdHeader: Record<string, string | number> = { ...M31_NO_SIP_HEADER, PLTSOLVD: 'T' };
    for (const key of ['PC1_1', 'PC1_2', 'PC2_1', 'PC2_2', 'CDELT1', 'CDELT2']) delete cdHeader[key];
    cdHeader['CD1_1'] = 0.000514216294499;
    cdHeader['CD1_2'] = -0.000142867911728;
    cdHeader['CD2_1'] = 0.000142892438101;
    cdHeader['CD2_2'] = 0.000514095720772;
    const sky = createPixelToSky(cdHeader)!(100, 2000);
    expect(separationArcseconds(sky, { raDegrees: 9.644271909710845, decDegrees: 41.32058833196306 })).toBeLessThan(0.1);
  });

  it('gives no sky position for a header that only says where the mount pointed', () => {
    expect(isPlateSolved(M31_NO_SIP_HEADER)).toBe(false);
    expect(createPixelToSky(M31_NO_SIP_HEADER)).toBeNull();
    expect(createPixelToSky({ ...M31_NO_SIP_HEADER, PLTSOLVD: 'F' })).toBeNull();
  });

  it('gives no sky position for a projection other than TAN, or a missing reference value', () => {
    expect(createPixelToSky({ ...M31_SIP_HEADER, CTYPE1: 'RA---SIN', CTYPE2: 'DEC--SIN' })).toBeNull();
    const withoutReference: Record<string, string | number> = { ...M31_SIP_HEADER };
    delete withoutReference['CRVAL1'];
    expect(createPixelToSky(withoutReference)).toBeNull();
  });
});

describe('sky position text', () => {
  it('writes right ascension as hours, minutes and seconds', () => {
    expect(formatRightAscension(10.8810120906)).toBe('00h 43m 31.44s');
    expect(formatRightAscension(0)).toBe('00h 00m 00.00s');
    // Rounding up to a whole day wraps to zero.
    expect(formatRightAscension(359.99999999)).toBe('00h 00m 00.00s');
  });

  it('writes declination with a sign, degrees, arcminutes and arcseconds', () => {
    expect(formatDeclination(41.1458844842)).toBe('+41° 08′ 45.2″');
    expect(formatDeclination(-0.5)).toBe('-00° 30′ 00.0″');
    // Rounding up carries into the arcminutes.
    expect(formatDeclination(10.99999999)).toBe('+11° 00′ 00.0″');
  });
});
