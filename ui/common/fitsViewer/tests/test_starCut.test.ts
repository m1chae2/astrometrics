/**
 * @file test_starCut.test.ts
 * @description Tests that the cut rebuilt in the browser from a measurement's cutout follows the backend's
 * recipe: samples every step along the angle, bilinear reads, and no point whose neighbors are missing.
 */
import { describe, expect, it } from 'vitest';
import { rebuildCut } from '../utils/starCut';
import type { CutSource } from '../utils/starCut';

/**
 * Makes a source whose cutout holds the value 10 x + y at image pixel (x, y), so a bilinear read is exact.
 *
 * @param overrides Fields to change.
 * @returns The source.
 */
function makeSource(overrides: Partial<CutSource> = {}): CutSource {
  const originX = 40;
  const originY = 109;
  return {
    center_x: 50.6,
    center_y: 119.8,
    cut_half_length_px: 10,
    cut_step_px: 0.5,
    cutout_origin_x: originX,
    cutout_origin_y: originY,
    cutout: Array.from({ length: 23 }, (_row, rowIndex) => Array.from({ length: 23 }, (_column, columnIndex) => 10 * (originX + columnIndex) + (originY + rowIndex))),
    ...overrides,
  };
}

describe('rebuildCut', () => {
  it.each([0, 30, 45, 90, 135, 179])('reproduces the exact ramp along a cut at %d degrees', (degrees) => {
    const source = makeSource();
    const cut = rebuildCut(source, degrees);
    const angle = (degrees * Math.PI) / 180;
    expect(cut.distance).toHaveLength(41);
    cut.distance.forEach((distance, index) => {
      expect(distance).toBeCloseTo(-10 + 0.5 * index, 9);
      const expected = 10 * (source.center_x + distance * Math.sin(angle)) + (source.center_y + distance * Math.cos(angle));
      expect(cut.values[index]).toBeCloseTo(expected, 6);
    });
  });

  it('keeps no point whose four neighbors are not all present', () => {
    const source = makeSource();
    // Blank the whole column x = 51 of the cutout (index 11): the cut along a column at x = 50.6 needs it.
    source.cutout = source.cutout.map((row) => row.map((value, index) => (index === 11 ? null : value)));
    expect(rebuildCut(source, 0).distance).toEqual([]);
    // Along a row, only the samples clear of that column survive: left of x = 50 or from x = 52 on.
    const alongRow = rebuildCut(source, 90);
    expect(alongRow.distance.length).toBeGreaterThan(0);
    expect(alongRow.distance.length).toBeLessThan(41);
    expect(alongRow.distance.every((distance) => 50.6 + distance < 50 || 50.6 + distance >= 52)).toBe(true);
  });

  it('drops points outside the cutout', () => {
    const cut = rebuildCut(makeSource({ cutout_origin_x: 49 }), 90);
    expect(cut.distance.length).toBeLessThan(41);
  });
});
