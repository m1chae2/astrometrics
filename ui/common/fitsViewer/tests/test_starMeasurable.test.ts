/**
 * @file test_starMeasurable.test.ts
 * @description Tests the "Measurable here?" rows built from a star profile measurement.
 */
import { describe, expect, it } from 'vitest';
import type { StarProfileMeasurement } from '../../types/backendTypes';
import { buildMeasurableRows } from '../utils/starMeasurable';

/**
 * Builds a measurement of a good, round, isolated star in the middle of a frame.
 *
 * @param overrides Fields to change.
 * @returns The measurement.
 */
function goodStar(overrides: Partial<StarProfileMeasurement> = {}): StarProfileMeasurement {
  return {
    star_found: true,
    clipped: false,
    clipped_reason: 'The peak is a single pixel, so there is no flat top.',
    peak_raw: 3000,
    image_maximum: 60000,
    moffat: { model: 'moffat', fitted: true, fwhm_px: 3.1 },
    gaussian: { model: 'gaussian', fitted: true, fwhm_px: 3.0 },
    ring_harmonics: [],
    signal_to_noise: 200,
    edge_distance_px: 500,
    coverage_fraction: 1,
    sky_outer_px: 12,
    circle_neighbor_count: 0,
    sky_ring_neighbor_count: 0,
    ...overrides,
  } as StarProfileMeasurement;
}

/**
 * Finds one row by id.
 *
 * @param rows The rows.
 * @param id The row name.
 * @returns The row, or undefined.
 */
const rowOf = (rows: ReturnType<typeof buildMeasurableRows>, id: string) => rows.find((row) => row.id === id);

describe('buildMeasurableRows', () => {
  it('says yes to every check for a good star and leaves out the edge and shape rows', () => {
    const rows = buildMeasurableRows(goodStar(), 3.0);
    expect(rows.map((row) => row.id)).toEqual(['width', 'saturation', 'signal', 'neighbors']);
    expect(rows.every((row) => row.ok)).toBe(true);
  });

  it('says no to a star much wider than the frame typical, and does not compare when the typical is unknown', () => {
    expect(rowOf(buildMeasurableRows(goodStar(), 1.5), 'width')?.ok).toBe(false);
    expect(rowOf(buildMeasurableRows(goodStar(), null), 'width')?.value).toBe('3.1 px');
  });

  it('flags a clipped star, a weak star and neighbors in the circle or the sky ring', () => {
    const rows = buildMeasurableRows(goodStar({ clipped: true, signal_to_noise: 6, circle_neighbor_count: 2, sky_ring_neighbor_count: 1 }), 3.0);
    expect(rowOf(rows, 'saturation')?.ok).toBe(false);
    expect(rowOf(rows, 'signal')?.ok).toBe(false);
    expect(rowOf(rows, 'neighbors')?.value).toBe('2 neighbors in the star circle, 1 neighbor in the sky ring');
  });

  it('adds an edge row near the edge or with low coverage', () => {
    const rows = buildMeasurableRows(goodStar({ edge_distance_px: 5, coverage_fraction: 0.7 }), 3.0);
    expect(rowOf(rows, 'edge')?.ok).toBe(false);
    expect(rowOf(rows, 'edge')?.value).toContain('5 px from the edge');
  });

  it('adds a shape row only for a noticeably oval star or something that is not a single star', () => {
    const oval = { order: 2, amplitude: 0.2, amplitude_error: 0.01, amplitude_debiased: 0.19, significance: 19, significant: true, direction_degrees: 10 };
    expect(rowOf(buildMeasurableRows(goodStar({ ring_harmonics: [oval] }), 3.0), 'shape')?.value).toBe('oval by 19%');
    const faint = { ...oval, amplitude_debiased: 0.02 };
    expect(rowOf(buildMeasurableRows(goodStar({ ring_harmonics: [faint] }), 3.0), 'shape')).toBeUndefined();
    expect(rowOf(buildMeasurableRows(goodStar({ star_found: false, not_a_star_reason: 'Too flat.' }), 3.0), 'shape')?.detail).toBe('Too flat.');
  });
});
