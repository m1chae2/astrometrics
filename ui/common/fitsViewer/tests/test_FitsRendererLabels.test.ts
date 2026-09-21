/**
 * @file test_FitsRendererLabels.test.ts
 * @description Unit tests for FitsRenderer label formatting helper (formatStarDisplayName).
 * Verifies catalog compression for 2MASS, GPM, SDSS, Gaia, and unnamed field stars.
 */

import { describe, expect, it } from 'vitest';
import { formatStarDisplayName } from '../FitsRenderer';

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
   * Tests empty or null inputs gracefully return an empty string.
   */
  it('handles empty input cleanly', () => {
    expect(formatStarDisplayName('')).toBe('');
  });
});
