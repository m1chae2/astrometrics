/**
 * @file test_starLabelImportance.test.ts
 * @description Tests for choosing star name badges by importance: proper names first, then Bayer and
 * Flamsteed, HD and HIP, TYC, then brightness; a cap that grows with the zoom; and no overlap.
 */
import { describe, expect, it } from 'vitest';
import {
  STAR_LABEL_BUDGET,
  StarScreenTransform,
  chooseLabeledStarIds,
  labelCapForScale,
  starNameRank
} from '../utils/starOverlayLayout';
import { estimateBadgeWidth } from '../FitsRenderer';
import type { AstrometryOverlayStar } from '../../services/astronomyService';

/**
 * Builds an overlay star.
 *
 * @param id Star id.
 * @param name Star name.
 * @param x Image column.
 * @param y Image row.
 * @param gMag Optional catalog magnitude.
 * @returns The overlay star.
 */
function star(id: string, name: string, x: number, y: number, gMag?: number): AstrometryOverlayStar {
  return { id, name, x, y, gMag, matchKind: 'matched' } as AstrometryOverlayStar;
}

const wide: StarScreenTransform = { scaleX: 1, scaleY: 1, panX: 0, panY: 0, viewWidth: 2000, viewHeight: 2000 };

describe('starNameRank', () => {
  /**
   * Each name style lands in its tier.
   */
  it('ranks proper, Bayer, Flamsteed, HD/HIP, TYC and other names', () => {
    expect(starNameRank('Vega')).toBe(0);
    expect(starNameRank('* alf Lyr')).toBe(1);
    expect(starNameRank('61 Cyg')).toBe(1);
    expect(starNameRank('HD 172167')).toBe(2);
    expect(starNameRank('HIP 91262')).toBe(2);
    expect(starNameRank('TYC 2656-1-1')).toBe(3);
    expect(starNameRank('Gaia DR3 123')).toBe(4);
  });
});

describe('chooseLabeledStarIds by importance', () => {
  /**
   * With room for only one label, the proper name beats brighter stars with catalog numbers.
   */
  it('prefers names by tier, then by brightness', () => {
    const stars = [
      star('a', 'TYC 1-2-3', 100, 100, 3),
      star('b', 'HD 1', 100, 110, 9),
      star('c', 'Vega', 100, 120, 12),
      star('d', 'HD 2', 100, 130, 5)
    ];
    expect(Array.from(chooseLabeledStarIds(stars, wide, estimateBadgeWidth, 1))).toEqual(['c']);
    const hdOnly = [stars[1], stars[3], stars[0]];
    expect(Array.from(chooseLabeledStarIds(hdOnly, wide, estimateBadgeWidth, 1))).toEqual(['d']);
  });

  /**
   * Two stars of one tier are ordered by magnitude.
   */
  it('orders one tier by catalog magnitude', () => {
    const stars = [star('faint', 'HD 1', 0, 0, 9), star('bright', 'HD 2', 0, 0, 2)];
    expect(Array.from(chooseLabeledStarIds(stars, wide, estimateBadgeWidth, 1))).toEqual(['bright']);
  });

  /**
   * The cap is small when fitted, grows when zoomed and is lifted when stars are far apart.
   */
  it('scales the cap with the zoom', () => {
    expect(labelCapForScale(0.2)).toBe(STAR_LABEL_BUDGET);
    expect(labelCapForScale(1)).toBeGreaterThan(labelCapForScale(0.5));
    expect(labelCapForScale(4)).toBe(Number.POSITIVE_INFINITY);
  });

  /**
   * No two chosen badges overlap, however many stars are packed together.
   */
  it('never overlaps badges', () => {
    const stars: AstrometryOverlayStar[] = [];
    for (let index = 0; index < 400; index++) {
      stars.push(star(`s${index}`, `HD ${index}`, (index % 20) * 9, Math.floor(index / 20) * 9, index));
    }
    const chosen = chooseLabeledStarIds(stars, { ...wide, scaleX: 2, scaleY: 2 }, estimateBadgeWidth);
    const boxes = stars.filter((item) => chosen.has(item.id)).map((item) => ({
      left: item.x * 2, right: item.x * 2 + 20 + estimateBadgeWidth(item), top: item.y * 2 - 12, bottom: item.y * 2 + 12
    }));
    expect(boxes.length).toBeGreaterThan(1);
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        expect(a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top).toBe(false);
      }
    }
  });
});
