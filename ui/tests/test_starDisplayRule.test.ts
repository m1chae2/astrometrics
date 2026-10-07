/**
 * @fileoverview Unit tests for the Planetarium's star display rule.
 *
 * Most of a local library's stars are detections around imaged targets whose
 * magnitude is empty or instrumental, so a magnitude cut can't thin them out.
 * These tests pin the rule that shows them only in a narrow FOV, and that the
 * generic Hipparcos/GAIA background sky is unaffected by it. Whether a
 * magnitude is a real catalog magnitude is the library's verdict, sent as
 * `hasCatalogMagnitude` (tested in wayfindinglib/test/test_sky_sources.py).
 */

import { describe, it, expect } from 'vitest';
import {
  formatCatalogMagnitude,
  isDisplayableStar,
  computeLimitingMagnitude,
  computeSourceBrightness,
  computeStarBrightness,
  STAR_MIN_BRIGHTNESS,
  UNCATALOGED_STAR_FULL_BRIGHTNESS_FOV_DEG,
  UNCATALOGED_STAR_MAX_FOV_DEG,
} from '../planetariumDisplay/layers/StarOverlay';

const WIDE_FOV_DEG = 90;
const NARROW_FOV_DEG = 2;

/** A real catalog magnitude, as the library marks it. */
const catalog = (magnitude: number) => ({ magnitude, hasCatalogMagnitude: true });
/** A magnitude the library says is not a catalog one (missing, 0 or instrumental). */
const notCatalog = (magnitude?: number) => ({ magnitude, hasCatalogMagnitude: false });

const localStar = (source: { magnitude?: number; hasCatalogMagnitude?: boolean }) =>
  ({ ra: 10, dec: 10, type: 'star', ...source }) as Parameters<typeof isDisplayableStar>[0];

const isShownLocally = (source: { magnitude?: number; hasCatalogMagnitude?: boolean }, fovDegrees: number) =>
  isDisplayableStar(localStar(source), true, true, computeLimitingMagnitude(fovDegrees), fovDegrees);

describe('formatCatalogMagnitude', () => {
  it('formats magnitudes the library marks as catalog magnitudes, including negative ones', () => {
    expect(formatCatalogMagnitude(catalog(6.2))).toBe('6.20');
    expect(formatCatalogMagnitude(catalog(-1.46))).toBe('-1.46');
    expect(formatCatalogMagnitude(catalog(12.3456), 3)).toBe('12.346');
  });

  it('shows anything the library does not mark as a catalog magnitude as unknown', () => {
    expect(formatCatalogMagnitude(notCatalog(0))).toBe('--');
    expect(formatCatalogMagnitude(notCatalog(-14.98))).toBe('--');
    expect(formatCatalogMagnitude(notCatalog())).toBe('--');
    expect(formatCatalogMagnitude({ magnitude: 6.2 })).toBe('--');
  });
});

describe('isDisplayableStar for the user\'s own stars', () => {
  it('hides stars with no catalog magnitude in a wide FOV', () => {
    expect(isShownLocally(notCatalog(), WIDE_FOV_DEG)).toBe(false);
    expect(isShownLocally(notCatalog(), WIDE_FOV_DEG)).toBe(false);
    expect(isShownLocally(notCatalog(-14.98), WIDE_FOV_DEG)).toBe(false);
  });

  it('shows stars with no catalog magnitude once the FOV is narrow enough', () => {
    expect(isShownLocally(notCatalog(), NARROW_FOV_DEG)).toBe(true);
    expect(isShownLocally(notCatalog(-14.98), NARROW_FOV_DEG)).toBe(true);
    expect(isShownLocally(notCatalog(), UNCATALOGED_STAR_MAX_FOV_DEG)).toBe(true);
    expect(isShownLocally(notCatalog(), UNCATALOGED_STAR_MAX_FOV_DEG + 0.1)).toBe(false);
  });

  it('still applies the limiting magnitude to stars that have a real one', () => {
    expect(isShownLocally(catalog(3.0), WIDE_FOV_DEG)).toBe(true);
    expect(isShownLocally(catalog(14.0), WIDE_FOV_DEG)).toBe(false);
    expect(isShownLocally(catalog(14.0), NARROW_FOV_DEG)).toBe(true);
  });

  it('respects the showCatalog toggle', () => {
    const limit = computeLimitingMagnitude(NARROW_FOV_DEG);
    expect(isDisplayableStar(localStar(catalog(3.0)), true, false, limit, NARROW_FOV_DEG)).toBe(false);
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
    const deepStar = { ra: 10, dec: 10, type: 'star', catalogSource: 'deep_stars', ...catalog(5) } as Parameters<
      typeof isDisplayableStar
    >[0];
    const limit = computeLimitingMagnitude(WIDE_FOV_DEG);
    expect(isDisplayableStar(deepStar, false, true, limit, WIDE_FOV_DEG)).toBe(false);
    expect(isDisplayableStar(deepStar, true, false, limit, WIDE_FOV_DEG)).toBe(true);
  });
});

describe('computeSourceBrightness', () => {
  it('shades a star with a real catalog magnitude by that magnitude at any FOV', () => {
    expect(computeSourceBrightness(catalog(3.0), WIDE_FOV_DEG)).toBe(computeStarBrightness(3.0));
    expect(computeSourceBrightness(catalog(3.0), NARROW_FOV_DEG)).toBe(computeStarBrightness(3.0));
  });

  it('draws a star with no catalog magnitude invisible at the FOV where it first appears', () => {
    expect(computeSourceBrightness(notCatalog(), UNCATALOGED_STAR_MAX_FOV_DEG)).toBe(0);
    expect(computeSourceBrightness(notCatalog(), UNCATALOGED_STAR_MAX_FOV_DEG + 5)).toBe(0);
  });

  it('draws a star with no catalog magnitude at the dimmest brightness once the fade is done', () => {
    expect(computeSourceBrightness(notCatalog(), UNCATALOGED_STAR_FULL_BRIGHTNESS_FOV_DEG)).toBe(STAR_MIN_BRIGHTNESS);
    expect(computeSourceBrightness(notCatalog(), NARROW_FOV_DEG)).toBe(STAR_MIN_BRIGHTNESS);
  });

  it('fades in smoothly with no jump between the two ends', () => {
    const halfwayFov = (UNCATALOGED_STAR_MAX_FOV_DEG + UNCATALOGED_STAR_FULL_BRIGHTNESS_FOV_DEG) / 2;
    const halfway = computeSourceBrightness(notCatalog(), halfwayFov);
    expect(halfway).toBeGreaterThan(0);
    expect(halfway).toBeLessThan(STAR_MIN_BRIGHTNESS);
    expect(computeSourceBrightness(notCatalog(), UNCATALOGED_STAR_MAX_FOV_DEG - 0.01)).toBeLessThan(0.01);
  });

  it('treats an instrumental magnitude like a missing one instead of drawing it at full brightness', () => {
    expect(computeSourceBrightness(notCatalog(-14.98), NARROW_FOV_DEG)).toBe(STAR_MIN_BRIGHTNESS);
    expect(computeSourceBrightness(notCatalog(-14.98), UNCATALOGED_STAR_MAX_FOV_DEG)).toBe(0);
  });
});
