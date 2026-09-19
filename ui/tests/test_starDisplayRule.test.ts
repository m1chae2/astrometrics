/**
 * @fileoverview Unit tests for the Planetarium's star display rule.
 *
 * Most of a local library's stars are detections around imaged targets whose
 * magnitude is empty or instrumental, so a magnitude cut can't thin them out.
 * These tests pin the rule that shows them only in a narrow FOV, and that the
 * generic Hipparcos/GAIA background sky is unaffected by it.
 */

import { describe, it, expect } from 'vitest';
import {
  formatCatalogMagnitude,
  hasCatalogMagnitude,
  isDisplayableStar,
  computeLimitingMagnitude,
  UNCATALOGED_STAR_MAX_FOV_DEG,
} from '../planetariumDisplay/layers/StarOverlay';

const WIDE_FOV_DEG = 90;
const NARROW_FOV_DEG = 2;

const localStar = (magnitude: unknown) =>
  ({ ra: 10, dec: 10, type: 'star', magnitude }) as Parameters<typeof isDisplayableStar>[0];

const isShownLocally = (magnitude: unknown, fovDegrees: number) =>
  isDisplayableStar(localStar(magnitude), true, true, computeLimitingMagnitude(fovDegrees), fovDegrees);

describe('formatCatalogMagnitude', () => {
  it('formats real catalog magnitudes, including negative ones', () => {
    expect(formatCatalogMagnitude(6.2)).toBe('6.20');
    expect(formatCatalogMagnitude(-1.46)).toBe('-1.46');
    expect(formatCatalogMagnitude(12.3456, 3)).toBe('12.346');
  });

  it('shows the legacy 0.0 placeholder as unknown rather than a measurement', () => {
    expect(formatCatalogMagnitude(0)).toBe('--');
  });

  it('shows missing, empty, non-finite and instrumental values as unknown', () => {
    expect(formatCatalogMagnitude(undefined)).toBe('--');
    expect(formatCatalogMagnitude(null)).toBe('--');
    expect(formatCatalogMagnitude('')).toBe('--');
    expect(formatCatalogMagnitude(NaN)).toBe('--');
    expect(formatCatalogMagnitude(-14.98)).toBe('--');
  });
});

describe('hasCatalogMagnitude', () => {
  it('accepts real apparent magnitudes, including the brightest stars', () => {
    expect(hasCatalogMagnitude(6.2)).toBe(true);
    expect(hasCatalogMagnitude(-1.46)).toBe(true);
  });

  it('rejects missing, empty-string, non-finite and instrumental values', () => {
    expect(hasCatalogMagnitude(undefined)).toBe(false);
    expect(hasCatalogMagnitude(null)).toBe(false);
    expect(hasCatalogMagnitude('')).toBe(false);
    expect(hasCatalogMagnitude(NaN)).toBe(false);
    expect(hasCatalogMagnitude(-14.98)).toBe(false);
  });
});

describe('isDisplayableStar for the user\'s own stars', () => {
  it('hides stars with no catalog magnitude in a wide FOV', () => {
    expect(isShownLocally('', WIDE_FOV_DEG)).toBe(false);
    expect(isShownLocally(undefined, WIDE_FOV_DEG)).toBe(false);
    expect(isShownLocally(-14.98, WIDE_FOV_DEG)).toBe(false);
  });

  it('shows stars with no catalog magnitude once the FOV is narrow enough', () => {
    expect(isShownLocally('', NARROW_FOV_DEG)).toBe(true);
    expect(isShownLocally(-14.98, NARROW_FOV_DEG)).toBe(true);
    expect(isShownLocally('', UNCATALOGED_STAR_MAX_FOV_DEG)).toBe(true);
    expect(isShownLocally('', UNCATALOGED_STAR_MAX_FOV_DEG + 0.1)).toBe(false);
  });

  it('still applies the limiting magnitude to stars that have a real one', () => {
    expect(isShownLocally(3.0, WIDE_FOV_DEG)).toBe(true);
    expect(isShownLocally(14.0, WIDE_FOV_DEG)).toBe(false);
    expect(isShownLocally(14.0, NARROW_FOV_DEG)).toBe(true);
  });

  it('respects the showCatalog toggle', () => {
    const limit = computeLimitingMagnitude(NARROW_FOV_DEG);
    expect(isDisplayableStar(localStar(3.0), true, false, limit, NARROW_FOV_DEG)).toBe(false);
  });
});

describe('isDisplayableStar for the online background sky', () => {
  it('is unaffected by the FOV rule for stars without a magnitude', () => {
    const hipparcosStar = { ra: 10, dec: 10, type: 'star', catalogSource: 'hipparcos' } as Parameters<
      typeof isDisplayableStar
    >[0];
    expect(isDisplayableStar(hipparcosStar, true, false, computeLimitingMagnitude(WIDE_FOV_DEG), WIDE_FOV_DEG)).toBe(
      true,
    );
  });

  it('follows showStars, not showCatalog', () => {
    const gaiaStar = { ra: 10, dec: 10, type: 'star', catalogSource: 'gaia', magnitude: 5 } as Parameters<
      typeof isDisplayableStar
    >[0];
    expect(isDisplayableStar(gaiaStar, false, true, computeLimitingMagnitude(WIDE_FOV_DEG), WIDE_FOV_DEG)).toBe(false);
  });
});
