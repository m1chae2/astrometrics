/**
 * @file test_FitsRendererLabels.test.ts
 * @description Unit tests for FitsRenderer label formatting helper (formatStarDisplayName).
 * Verifies catalog compression for 2MASS, GPM, SDSS, Gaia, and unnamed field stars.
 */

import { describe, expect, it } from 'vitest';
import { computeBadgePlacement, formatStarDisplayName, isStarSelected } from '../FitsRenderer';

describe('formatStarDisplayName', () => {
  /**
   * Tests 2MASS coordinate compression to concise HUD badge notation.
   */
  it('compresses lengthy 2MASS coordinate identifiers into short sky notation', () => {
    expect(formatStarDisplayName('2MASS J16413289+3626285')).toBe('2MASS 1641+3626');
    expect(formatStarDisplayName('2MASS 16413289-3626285')).toBe('2MASS 1641-3626');
  });

  /**
   * Tests GPM survey coordinate compression.
   */
  it('compresses GPM survey identifiers into integer degree coordinates', () => {
    expect(formatStarDisplayName('GPM 258.328551+36.535702')).toBe('GPM 258+36');
    expect(formatStarDisplayName('GPM 12.345678-05.987654')).toBe('GPM 12-05');
  });

  /**
   * Tests SDSS survey coordinate compression.
   */
  it('compresses SDSS coordinate identifiers into short sky notation', () => {
    expect(formatStarDisplayName('SDSS J164132.89+362628.5')).toBe('SDSS 1641+3626');
  });

  /**
   * Tests Gaia DR3 identifier formatting with ellipsis preservation of distinguishing digits.
   */
  it('shortens Gaia DR3 long integer strings preserving trailing distinguishing digits', () => {
    expect(formatStarDisplayName('Gaia DR3 1327617929278818176')).toBe('Gaia DR3 …818176');
  });

  /**
   * Tests position-only unnamed field detection formatting.
   */
  it('formats unnamed FIELD_J coordinates to clean degrees', () => {
    expect(formatStarDisplayName('FIELD_J249.8348+35.5319')).toBe('249.83 +35.53');
  });

  /**
   * Tests standard catalog names and Bayer/Flamsteed designations remain unchanged.
   */
  it('leaves standard catalog and named stars unchanged', () => {
    expect(formatStarDisplayName('HD 150679')).toBe('HD 150679');
    expect(formatStarDisplayName('Vega')).toBe('Vega');
    expect(formatStarDisplayName('TYC 3105-899-1')).toBe('TYC 3105-899-1');
  });

  /**
   * Tests cluster member star names strip redundant cluster catalog prefixes.
   */
  it('strips redundant cluster catalog prefixes from member star names', () => {
    expect(formatStarDisplayName('CI* NGC 6205 KAD 656')).toBe('KAD 656');
    expect(formatStarDisplayName('Cl* NGC 6205 KAD 123')).toBe('KAD 123');
  });

  /**
   * Tests empty or null inputs gracefully return an empty string.
   */
  it('handles empty input cleanly', () => {
    expect(formatStarDisplayName('')).toBe('');
  });
});

describe('isStarSelected', () => {
  const sampleStar = {
    id: 'CI* NGC 6205 KAD 656',
    name: 'CI* NGC 6205 KAD 656',
    x: 100,
    y: 200,
    isCatalogIdentified: true,
  };

  /**
   * Tests exact and case-insensitive matching by ID and name.
   */
  it('matches exact and case-insensitive star identifiers', () => {
    expect(isStarSelected(sampleStar, 'CI* NGC 6205 KAD 656')).toBe(true);
    expect(isStarSelected(sampleStar, 'ci* ngc 6205 kad 656')).toBe(true);
  });

  /**
   * Tests catalog substring matching between full names and concise identifiers.
   */
  it('matches catalog substring variants', () => {
    expect(isStarSelected(sampleStar, 'KAD 656')).toBe(true);
    expect(isStarSelected({ ...sampleStar, id: 'KAD 656' }, 'CI* NGC 6205 KAD 656')).toBe(true);
    // Cluster designation prefix variance: Cl* (lowercase L) vs CI* (capital I)
    expect(isStarSelected(sampleStar, 'Cl* NGC 6205 KAD 656')).toBe(true);
  });

  /**
   * Tests non-matching star identifiers return false.
   */
  it('returns false for mismatched star identifiers', () => {
    expect(isStarSelected(sampleStar, 'HD 150679')).toBe(false);
    expect(isStarSelected(sampleStar, null)).toBe(false);
    expect(isStarSelected(sampleStar, undefined)).toBe(false);
  });
});

describe('computeBadgePlacement', () => {
  /**
   * Tests default placement on the right side of the star reticle.
   */
  it('places badge on the right side (+20px) by default when within viewport boundaries', () => {
    // Star at x=200, badgeWidth=80 in container of width 1000
    // screenX + 20 + badgeWidth = 300 <= 1000 => place right (+20)
    expect(computeBadgePlacement(200, 80, 1000)).toBe(20);
  });

  /**
   * Tests badge flipping to the left side when placing on right would overflow container boundary.
   */
  it('flips badge to the left side when right placement would overflow the container right edge', () => {
    // Star at x=950, badgeWidth=80 in container of width 1000
    // screenX + 20 + badgeWidth = 1050 > 1000 => place left (-badgeWidth - 20 = -100)
    expect(computeBadgePlacement(950, 80, 1000)).toBe(-100);
  });

  /**
   * Tests boundary edge condition when badge exactly reaches right edge.
   */
  it('places on right when badge right edge exactly touches container boundary', () => {
    // Star at x=900, badgeWidth=80 in container of width 1000: 900 + 20 + 80 = 1000 <= 1000
    expect(computeBadgePlacement(900, 80, 1000)).toBe(20);
    // 1px further: 901 + 20 + 80 = 1001 > 1000 => flips left
    expect(computeBadgePlacement(901, 80, 1000)).toBe(-100);
  });
});
