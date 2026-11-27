/**
 * @file test_starRing.test.ts
 * @description Tests the "around the star" helpers: the order names, the one-line summary of the significant terms
 * (strongest first, only the one-sided term names a direction), the smooth curve of the significant terms, the
 * few-pixels check, and the screen direction of an angle for either row order.
 */
import { describe, expect, it } from 'vitest';
import type { RingHarmonic } from '../../types/backendTypes';
import { describeRing, formatPercent, harmonicCurve, hasFewPixels, orderName, screenDirection } from '../utils/starRing';

/**
 * Makes one Fourier term.
 *
 * @param order The number of lobes.
 * @param amplitude The debiased amplitude, as a fraction.
 * @param significant Whether the term is significant.
 * @param direction The direction of the brightest lobe, in degrees.
 * @returns The term.
 */
function term(order: number, amplitude: number, significant: boolean, direction = 0): RingHarmonic {
  return {
    order,
    amplitude,
    amplitude_error: 0.02,
    amplitude_debiased: amplitude,
    significance: significant ? 5 : 1,
    significant,
    direction_deg: direction,
  };
}

describe('orderName', () => {
  it('names the first four orders and calls the rest m-fold', () => {
    expect([1, 2, 3, 4, 5, 8].map(orderName)).toEqual(['One-sided', 'Oval', 'Three-fold', 'Four-fold', '5-fold', '8-fold']);
  });
});

describe('describeRing', () => {
  it('says the star is round when no term is significant', () => {
    expect(describeRing([term(1, 0.03, false), term(2, 0.02, false)], true)).toBe('Round within the noise');
    expect(describeRing([], true)).toBe('Round within the noise');
  });

  it('lists the significant terms, biggest first, with a direction only for the one-sided term', () => {
    const terms = [term(2, 0.03, true), term(1, 0.12, true, 170), term(3, 0.2, false), term(5, 0.015, true)];
    // 170 degrees is nearest to 180: down the screen for a bottom-up picture, up for a top-down one.
    expect(describeRing(terms, false)).toBe('One-sided 12% ± 2.0% toward Down; oval 3.0% ± 2.0%; 5-fold 1.5% ± 2.0%');
    expect(describeRing(terms, true)).toContain('toward Up');
  });

  it('names at most three terms', () => {
    const terms = [1, 2, 3, 4, 5].map((order) => term(order, 0.1 - 0.01 * order, true));
    expect(describeRing(terms, true).split(';')).toHaveLength(3);
  });

  it('writes percents with a decimal under 10%', () => {
    expect([0.124, 0.034, 0.0994, 0.0996, 0.1].map(formatPercent)).toEqual(['12%', '3.4%', '9.9%', '10%', '10%']);
  });
});

describe('harmonicCurve', () => {
  const source = (terms: RingHarmonic[]) => ({
    ring_bin_angle_deg: [], ring_bin_counts: [], ring_harmonics: terms, ring_constant: 0.5, ring_scale_value: 20,
  });

  it('is the constant times the scale when nothing is significant', () => {
    const curve = harmonicCurve(source([term(1, 0.5, false)]));
    expect(curve?.angles[0]).toBe(0);
    expect(curve?.angles[curve.angles.length - 1]).toBe(360);
    expect(new Set(curve?.values.map((value) => value.toFixed(9)))).toEqual(new Set(['10.000000000']));
  });

  it('adds each significant term as a cosine peaking at its direction', () => {
    const curve = harmonicCurve(source([term(1, 0.2, true, 90), term(3, 0.1, true, 0)]));
    const at = (angle: number) => curve!.values[curve!.angles.indexOf(angle)];
    expect(at(90)).toBeCloseTo(10 * (1 + 0.2 + 0.1 * Math.cos((3 * 90 * Math.PI) / 180)), 9);
    expect(at(270)).toBeCloseTo(10 * (1 - 0.2 + 0.1 * Math.cos((3 * 270 * Math.PI) / 180)), 9);
    // The brightest direction of the one-sided term is 90 degrees, the dimmest 270.
    expect(Math.max(...curve!.values.filter((_value, index) => curve!.angles[index] % 90 === 0))).toBe(at(90) > at(0) ? at(90) : at(0));
  });

  it('is null when the ring was not analyzed', () => {
    expect(harmonicCurve({ ...source([]), ring_constant: null })).toBeNull();
    expect(harmonicCurve(source([]))).toBeNull();
  });
});

describe('hasFewPixels', () => {
  it('is false for a full ring of steady bins, and true for a thin bin or a missing bin', () => {
    const angles = Array.from({ length: 72 }, (_value, index) => 2.5 + 5 * index);
    expect(hasFewPixels(new Array(72).fill(12), angles)).toBe(false);
    expect(hasFewPixels([...new Array(71).fill(12), 3], angles)).toBe(true);
    expect(hasFewPixels(new Array(70).fill(12), angles.filter((_angle, index) => index !== 10 && index !== 11))).toBe(true);
    expect(hasFewPixels([], [])).toBe(true);
  });
});

describe('screenDirection', () => {
  it('turns an angle into the direction it points to on the screen', () => {
    expect([0, 90, 180, 270].map((angle) => screenDirection(angle, true))).toEqual(['Down', 'Right', 'Up', 'Left']);
    expect([0, 90, 180, 270].map((angle) => screenDirection(angle, false))).toEqual(['Up', 'Right', 'Down', 'Left']);
  });

  it('rounds any other angle to the nearest axis', () => {
    expect(screenDirection(170, false)).toBe('Down');
    expect(screenDirection(350, false)).toBe('Up');
  });
});
