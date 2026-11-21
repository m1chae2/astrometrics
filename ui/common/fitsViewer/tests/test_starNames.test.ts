/**
 * @fileoverview Tests for the name shown at the top of the star profile panel: the overlay star at the
 * measured center, or "Unnamed star" with its sky position on a plate-solved image.
 */
import { describe, expect, it } from 'vitest';
import { canOpenInAstronomyManager, catalogKeysOfStar, nameStarAt, UNNAMED_STAR } from '../utils/starNames';

const solved = () => ({ raDegrees: 10.684708, decDegrees: 41.26875 });

describe('nameStarAt', () => {
  it('uses the overlay star at the measured center', () => {
    const stars = [{ x: 99, y: 49, name: 'alf Lyr' }, { x: 10, y: 10, name: 'HD 1' }];
    expect(nameStarAt(100.5, 50.2, stars, solved)).toBe('alf Lyr');
  });

  it('calls a detection with no catalog star an unnamed star at its sky position', () => {
    const stars = [{ x: 99, y: 49, name: 'Unnamed star', matchKind: 'unmatched' }];
    expect(nameStarAt(100, 50, stars, solved)).toBe(`${UNNAMED_STAR} at 00h 42m 44.33s +41° 16′ 07.5″`);
  });

  it('gives no name on an image with no stars and no plate solution', () => {
    expect(nameStarAt(100, 50, undefined, null)).toBeNull();
    expect(nameStarAt(100, 50, [{ x: 0, y: 0, name: 'far away' }], null)).toBeNull();
  });
});

describe('canOpenInAstronomyManager', () => {
  /**
   * A star opens when the library has a record of it, whatever its name looks like.
   */
  it('accepts a star with a library record, Gaia DR3-named ones included, and refuses the rest', () => {
    expect(canOpenInAstronomyManager({ libraryId: 'Gaia DR3 123' })).toBe(true);
    expect(canOpenInAstronomyManager({ libraryId: 'Vega' })).toBe(true);
    expect(canOpenInAstronomyManager({ libraryId: null })).toBe(false);
    expect(canOpenInAstronomyManager({})).toBe(false);
  });
});

describe('catalogKeysOfStar', () => {
  /**
   * The Gaia DR3 id comes first, then the name; a detection id and an unnamed star give no key.
   */
  it('lists the Gaia DR3 id then the name, never a detection id', () => {
    expect(catalogKeysOfStar({ name: 'HD 214181', gaiaSourceId: '99' })).toEqual(['Gaia DR3 99', 'HD 214181']);
    expect(catalogKeysOfStar({ name: 'HD 1' })).toEqual(['HD 1']);
    expect(catalogKeysOfStar({ name: UNNAMED_STAR })).toEqual([]);
    expect(catalogKeysOfStar({ name: 'Gaia DR3 99', gaiaSourceId: '99' })).toEqual(['Gaia DR3 99']);
    expect(catalogKeysOfStar({ id: 'detection-5', name: 'HD 1' })).toEqual(['HD 1']);
    expect(catalogKeysOfStar({ id: 'Gaia DR3 7', name: 'Gaia DR3 7' })).toEqual(['Gaia DR3 7']);
    expect(catalogKeysOfStar({ id: 'HIP 5', name: 'Vega' })).toEqual(['Vega', 'HIP 5']);
  });
});
