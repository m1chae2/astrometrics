/**
 * @file starMeasurable.ts
 * @description Turns one star profile measurement into the short "Measurable here?" list of the star
 * inspector: plain yes or no rows with the value behind each. The checks are rough guides for a person
 * who is choosing a star to trust. They are not limits the library applies.
 */
import type { StarProfileMeasurement } from '../../types/backendTypes';

/** A star is called well measured when its peak is this many times the sky noise. The star finder's own floor is 5, so 10 leaves room above it. */
export const GOOD_SIGNAL_TO_NOISE = 10;

/** A star's width counts as typical when it is within this share of the frame's typical width. */
export const WIDTH_TYPICAL_RANGE = { low: 0.7, high: 1.5 } as const;

/** The share of the sky ring that must lie on the image before the edge is not a concern. */
export const GOOD_COVERAGE_FRACTION = 0.95;

/** An oval term of the ring is worth showing when it is at least this share of the ring's mean brightness. */
export const NOTICEABLE_OVAL_AMPLITUDE = 0.05;

/** One line of the list. */
export interface MeasurableRow {
  /** A stable name, used for test ids. */
  id: 'width' | 'saturation' | 'signal' | 'neighbors' | 'edge' | 'shape';
  /** True when the star is fine for this check. */
  ok: boolean;
  /** The question, for example "Width". */
  label: string;
  /** The value, for example "3.1 px, typical 3.0 px". */
  value: string;
  /** A sentence that says why, for the tooltip. */
  detail: string;
}

/**
 * Picks the width of the fitted curve (the Moffat fit, or the Gaussian when that one failed).
 *
 * @param measurement The backend's answer.
 * @returns The width in pixels, or null when no fit worked.
 */
function fittedWidth(measurement: StarProfileMeasurement): number | null {
  const fit = measurement.moffat.fitted ? measurement.moffat : measurement.gaussian.fitted ? measurement.gaussian : null;
  const width = fit?.fwhm_px;
  return width !== null && width !== undefined && Number.isFinite(width) ? width : null;
}

/**
 * Writes a count of neighbors, for example "2 neighbors".
 *
 * @param count How many.
 * @returns The words.
 */
function neighborWords(count: number): string {
  return `${count} ${count === 1 ? 'neighbor' : 'neighbors'}`;
}

/**
 * Builds the rows of the "Measurable here?" list.
 *
 * @param measurement The backend's answer for the star.
 * @param frameWidthPx The typical star width of the frame, in pixels, or null when it is not known.
 * @returns The rows, in reading order. The edge and shape rows appear only when they apply.
 */
export function buildMeasurableRows(measurement: StarProfileMeasurement, frameWidthPx: number | null): MeasurableRow[] {
  const rows: MeasurableRow[] = [];
  // The backend sends these with defaults, so an older answer may leave them out.
  const signalToNoise = measurement.signal_to_noise ?? 0;
  const edgeDistance = measurement.edge_distance_px ?? Infinity;
  const coverage = measurement.coverage_fraction ?? 1;
  const skyOuter = measurement.sky_outer_px ?? 0;

  const width = fittedWidth(measurement);
  if (width === null) {
    rows.push({ id: 'width', ok: false, label: 'Width', value: 'not fitted', detail: 'The star shape could not be fitted, so there is no width.' });
  } else if (frameWidthPx !== null && frameWidthPx > 0) {
    const ratio = width / frameWidthPx;
    const typical = ratio >= WIDTH_TYPICAL_RANGE.low && ratio <= WIDTH_TYPICAL_RANGE.high;
    rows.push({
      id: 'width',
      ok: typical,
      label: 'Width',
      value: `${width.toFixed(1)} px, frame typical ${frameWidthPx.toFixed(1)} px`,
      detail: typical
        ? 'This star is about as wide as the stars on this frame.'
        : ratio > WIDTH_TYPICAL_RANGE.high
          ? 'This star is much wider than the others on the frame. It may be two stars blended, or not a star.'
          : 'This star is much narrower than the others on the frame. It may be a hot pixel or noise.',
    });
  } else {
    rows.push({ id: 'width', ok: true, label: 'Width', value: `${width.toFixed(1)} px`, detail: 'The frame\'s typical width is not known yet, so it is not compared.' });
  }

  rows.push({
    id: 'saturation',
    ok: !measurement.clipped,
    label: 'Not saturated',
    value: measurement.clipped ? 'clipped' : `peak is ${((measurement.peak_raw / Math.max(measurement.image_maximum, 1e-12)) * 100).toFixed(0)}% of the image maximum`,
    detail: measurement.clipped_reason,
  });

  rows.push({
    id: 'signal',
    ok: signalToNoise >= GOOD_SIGNAL_TO_NOISE,
    label: 'Bright enough',
    value: `signal-to-noise ${signalToNoise.toFixed(0)}`,
    detail: `The peak is ${signalToNoise.toFixed(1)} times the noise of the sky. At ${GOOD_SIGNAL_TO_NOISE} or more the star stands well above the noise.`,
  });

  const circle = measurement.circle_neighbor_count ?? 0;
  const ring = measurement.sky_ring_neighbor_count ?? 0;
  const neighborParts: string[] = [];
  if (circle > 0) neighborParts.push(`${neighborWords(circle)} in the star circle`);
  if (ring > 0) neighborParts.push(`${neighborWords(ring)} in the sky ring`);
  rows.push({
    id: 'neighbors',
    ok: circle === 0 && ring === 0,
    label: 'No close neighbors',
    value: neighborParts.length > 0 ? neighborParts.join(', ') : 'none',
    detail: 'A bright neighbor in the star circle adds its light to the star. One in the sky ring makes the sky look brighter than it is.',
  });

  const nearEdge = edgeDistance < skyOuter || coverage < GOOD_COVERAGE_FRACTION;
  if (nearEdge) {
    rows.push({
      id: 'edge',
      ok: false,
      label: 'Away from the edge',
      value: `${edgeDistance.toFixed(0)} px from the edge, ${(coverage * 100).toFixed(0)}% of the sky ring on the image`,
      detail: 'Near the edge, part of the sky ring is missing, so the sky level is less certain.',
    });
  }

  if (!measurement.star_found) {
    rows.push({ id: 'shape', ok: false, label: 'Single star', value: 'no', detail: measurement.not_a_star_reason ?? 'This does not look like a single star.' });
  } else {
    const oval = measurement.ring_harmonics.find((term) => term.order === 2 && term.significant && term.amplitude_debiased >= NOTICEABLE_OVAL_AMPLITUDE);
    if (oval) {
      rows.push({
        id: 'shape',
        ok: false,
        label: 'Round',
        value: `oval by ${(oval.amplitude_debiased * 100).toFixed(0)}%`,
        detail: 'The light around the star is stretched along one direction, for example by tracking drift or a close pair.',
      });
    }
  }
  return rows;
}
