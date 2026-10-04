/**
 * @fileoverview Tests for the linear display range of the FITS viewer.
 *
 * A stretched picture holds values from 0 to 1. Drawn over its own darkest and
 * brightest pixels, its sky turns almost black and its bright stars clip. These
 * tests check that the linear parameters follow the range they are given.
 */

import { describe, it, expect } from 'vitest';
import { computeLinearStretchParameters } from '../common/fitsViewer/mtfStretchGL';

describe('computeLinearStretchParameters', () => {
  it('maps the given range to black and white', () => {
    expect(computeLinearStretchParameters(0.137, 0.917)).toEqual({ shadows: 0.137, range: 0.917 - 0.137, midtones: 0.5 });
  });

  it('keeps a stretched picture on its 0 to 1 scale, so a sky at 0.19 stays dark grey', () => {
    const { shadows, range } = computeLinearStretchParameters(0, 1);
    const skyBrightness = (0.19 - shadows) / range;
    expect(skyBrightness).toBeCloseTo(0.19, 6);
  });

  it('avoids a zero range for a flat image', () => {
    expect(computeLinearStretchParameters(0.5, 0.5).range).toBe(1);
  });
});
