/**
 * @file starCut.ts
 * @description Rebuilds the straight cut through a star at any angle from the small piece of the image
 * (the cutout) that the backend sends with each star measurement. The slider can then move the cut with
 * no new call to the backend. The recipe is the one in the backend's model of the measurement.
 */
import type { StarProfileMeasurement } from '../../types/backendTypes';

/** The pieces of a measurement that the cut needs. */
export type CutSource = Pick<
  StarProfileMeasurement,
  'center_x' | 'center_y' | 'cut_half_length_px' | 'cut_step_px' | 'cutout' | 'cutout_origin_x' | 'cutout_origin_y'
>;

/** A cut: the signed distance of each sample from the center, and the value there. */
export interface StarCut {
  /** Distance from the center, in pixels; positive toward the direction of the angle. */
  distance: number[];
  /** Value above the sky at each distance. */
  values: number[];
}

/**
 * Reads the cutout at one position by bilinear interpolation.
 *
 * @param source The measurement.
 * @param imageX Column, counted from 1 as in FITS.
 * @param imageY Row, counted from 1 as in FITS.
 * @returns The value, or null when any of the four surrounding pixels is outside the cutout, outside the image or not finite.
 */
function sampleCutout(source: CutSource, imageX: number, imageY: number): number | null {
  const column = imageX - source.cutout_origin_x;
  const row = imageY - source.cutout_origin_y;
  const column0 = Math.floor(column);
  const row0 = Math.floor(row);
  const columnFraction = column - column0;
  const rowFraction = row - row0;
  const corner = (rowIndex: number, columnIndex: number): number | null => source.cutout[rowIndex]?.[columnIndex] ?? null;
  const lowLow = corner(row0, column0);
  const lowHigh = corner(row0, column0 + 1);
  const highLow = corner(row0 + 1, column0);
  const highHigh = corner(row0 + 1, column0 + 1);
  if (lowLow === null || lowHigh === null || highLow === null || highHigh === null) return null;
  return (
    lowLow * (1 - rowFraction) * (1 - columnFraction)
    + lowHigh * (1 - rowFraction) * columnFraction
    + highLow * rowFraction * (1 - columnFraction)
    + highHigh * rowFraction * columnFraction
  );
}

/**
 * Builds the cut through the star's center at an angle. The angle runs from +y (up a column) toward +x.
 *
 * @param source The measurement, with its cutout.
 * @param angleDeg The angle of the cut.
 * @param halfLengthPx How far the cut reaches each way (default: the length the measurement was made with). A longer one than the cutout covers just loses its outer points.
 * @returns The samples, in order of distance, without the points that have no value.
 */
export function rebuildCut(source: CutSource, angleDeg: number, halfLengthPx: number = source.cut_half_length_px): StarCut {
  const angle = (angleDeg * Math.PI) / 180;
  const sine = Math.sin(angle);
  const cosine = Math.cos(angle);
  const stepCount = Math.floor(halfLengthPx / source.cut_step_px);
  const cut: StarCut = { distance: [], values: [] };
  for (let index = -stepCount; index <= stepCount; index++) {
    const distance = index * source.cut_step_px;
    const value = sampleCutout(source, source.center_x + distance * sine, source.center_y + distance * cosine);
    if (value !== null) {
      cut.distance.push(distance);
      cut.values.push(value);
    }
  }
  return cut;
}
