/**
 * @fileoverview Tests of the one star-density rule every Planetarium star layer follows: a
 * magnitude limit that tightens with the view, and at most a fixed number of dots per screen
 * area, brightest first, always keeping the selected star.
 */

import { describe, expect, it } from 'vitest';
import {
  CATALOG_STAR_DEFAULT_MAGNITUDE, computeLimitingMagnitude, limitStarDensity, MAX_STARS_PER_MEGAPIXEL,
} from '../layers/StarOverlay';
import type { DrawableStar } from '../layers/StarOverlay';

/**
 * Makes on-screen stars with magnitudes 1, 2, 3, ...
 * @param count How many.
 * @returns The stars.
 */
function stars(count: number): DrawableStar[] {
  return Array.from({ length: count }, (_, index) => ({
    source: { id: `star ${index}`, ra: 1, dec: 1, name: '', type: 'star', magnitude: 1 + index / 100, hasCatalogMagnitude: true, hasSpectra: false, hasPhotometry: false },
    x: 10,
    y: 10,
  }));
}

describe('star density rule', () => {
  /** The limit tightens with the view: sparse wide, deeper when zoomed in. */
  it('lets fainter stars in only as the view narrows', () => {
    expect(computeLimitingMagnitude(90)).toBeCloseTo(6, 5);
    expect(computeLimitingMagnitude(15)).toBeLessThan(10);
    expect(computeLimitingMagnitude(2)).toBeGreaterThan(computeLimitingMagnitude(15));
    expect(computeLimitingMagnitude(0.5)).toBeGreaterThan(computeLimitingMagnitude(2));
  });

  /** A crowded screen keeps only the brightest, up to the per-area cap. */
  it('keeps the brightest stars up to the cap', () => {
    const cap = Math.round(1 * MAX_STARS_PER_MEGAPIXEL); // a 1000 x 1000 screen
    const drawn = limitStarDensity(stars(cap + 500), 1000, 1000);
    expect(drawn).toHaveLength(cap);
    expect(Math.max(...drawn.map((star) => star.source.magnitude as number))).toBeLessThan(1 + cap / 100);
  });

  /** The selected star is kept even when it is the faintest. */
  it('always keeps the selected star', () => {
    const all = stars(MAX_STARS_PER_MEGAPIXEL + 100);
    const faintest = all[all.length - 1].source.id;
    const drawn = limitStarDensity(all, 1000, 1000, faintest);
    expect(drawn.some((star) => star.source.id === faintest)).toBe(true);
  });

  /**
   * A catalog-layer star with no magnitude ranks at the same default magnitude the drawing rule
   * gives it, so it is kept ahead of fainter stars; one of the user's own stars with none ranks last.
   */
  it('ranks a catalog star with no magnitude at the default magnitude', () => {
    // Magnitudes 1 to 1 + count/100: the cap falls well past the default magnitude.
    const count = MAX_STARS_PER_MEGAPIXEL + 100;
    expect(1 + MAX_STARS_PER_MEGAPIXEL / 100).toBeGreaterThan(CATALOG_STAR_DEFAULT_MAGNITUDE);
    const catalogStar: DrawableStar = {
      source: { id: 'hip', ra: 1, dec: 1, name: '', type: 'star', catalogSource: 'hipparcos', hasSpectra: false, hasPhotometry: false },
      x: 10, y: 10,
    };
    const ownStar: DrawableStar = {
      source: { id: 'own', ra: 1, dec: 1, name: '', type: 'star', hasSpectra: false, hasPhotometry: false },
      x: 10, y: 10,
    };
    const drawn = limitStarDensity([...stars(count), catalogStar, ownStar], 1000, 1000);
    expect(drawn.some((star) => star.source.id === 'hip')).toBe(true);
    expect(drawn.some((star) => star.source.id === 'own')).toBe(false);
  });

  /** Under the cap, nothing is dropped. */
  it('leaves a sparse screen alone', () => {
    const few = stars(10);
    expect(limitStarDensity(few, 1000, 1000)).toBe(few);
  });
});
