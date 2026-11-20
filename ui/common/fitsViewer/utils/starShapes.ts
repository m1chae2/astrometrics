/**
 * @file starShapes.ts
 * @description Turns the star shapes the backend measured into what the Shapes overlay draws and says. Each
 * star gets one short line through its position along its long axis, as long as its stretch. The words of
 * the frame summary (shown in a corner of the viewer and under the star profile header) and the comparison
 * of a clicked star with the frame are built here too.
 *
 * Angles run from +y toward +x, like the cut of the star profile: 0° is straight up and down the file's
 * rows, 90° is along its columns.
 */
import type { StarShape, StarShapesMeasurement } from '../../types/backendTypes';
import type { DetectionGeometry } from './detectionMarkers';
import { detectionScreenPosition } from './detectionMarkers';

/** Screen pixels of line length for a stretch of 1 (a star as long as the sum of its sides). */
export const SHAPE_LINE_LENGTH_PER_STRETCH_PX = 80;

/** The longest a line may be, in screen pixels, so one trailed star does not cross the picture. */
export const SHAPE_LINE_MAXIMUM_LENGTH_PX = 60;

/** Lines shorter than this, in screen pixels, are left out: a round star shows nothing. */
export const SHAPE_LINE_MINIMUM_LENGTH_PX = 2;

/** A star stretched less than this (a fraction) is called round in words. */
export const ROUND_STRETCH_FRACTION = 0.05;

/** A star stretched less than this (a fraction) is called slightly oval in words. */
export const SLIGHTLY_OVAL_STRETCH_FRACTION = 0.15;

/** A clicked star this close (image pixels) to a measured star is taken to be that star. */
export const SAME_STAR_DISTANCE_PX = 3;

/** The SVG path data of the lines in view, and how many there are. */
export interface ShapeLinePaths {
  /** One `M x y L x y` segment for each star with a visible line. */
  lines: string;
  /** How many lines are drawn. */
  visibleCount: number;
}

/**
 * Builds the path data of the lines of every star in view.
 *
 * @param shapes The measured stars.
 * @param geometry How the picture sits in the viewer.
 * @returns The lines, as one path.
 */
export function buildShapeLinePaths(shapes: StarShape[], geometry: DetectionGeometry): ShapeLinePaths {
  const segments: string[] = [];
  const margin = SHAPE_LINE_MAXIMUM_LENGTH_PX;
  for (const shape of shapes) {
    const length = Math.min(shape.elongation * SHAPE_LINE_LENGTH_PER_STRETCH_PX, SHAPE_LINE_MAXIMUM_LENGTH_PX);
    if (length < SHAPE_LINE_MINIMUM_LENGTH_PX) continue;
    const { x, y } = detectionScreenPosition(shape, geometry);
    if (x < -margin || y < -margin || x > geometry.viewWidth + margin || y > geometry.viewHeight + margin) continue;
    // The direction is (sin, cos) in (x, y) of the file, like the cut. A screen row grows downward, so the
    // y part flips when the file's first row is at the bottom.
    const angle = (shape.angle_degrees * Math.PI) / 180;
    const alongX = (Math.sin(angle) * length) / 2;
    const alongY = (Math.cos(angle) * length) / 2 * (geometry.firstRowAtTop ? 1 : -1);
    segments.push(
      `M${(x - alongX).toFixed(1)} ${(y - alongY).toFixed(1)}L${(x + alongX).toFixed(1)} ${(y + alongY).toFixed(1)}`,
    );
  }
  return { lines: segments.join(''), visibleCount: segments.length };
}

/**
 * Words for a stretch.
 *
 * @param stretch The stretch, as a fraction (0 is round).
 * @returns "round", "slightly oval" or "oval".
 */
export function describeStretch(stretch: number): string {
  if (stretch < ROUND_STRETCH_FRACTION) return 'round';
  if (stretch < SLIGHTLY_OVAL_STRETCH_FRACTION) return 'slightly oval';
  return 'oval';
}

/**
 * Writes a stretch as a whole percent.
 *
 * @param stretch The stretch, as a fraction.
 * @returns For example "29%".
 */
export function formatStretch(stretch: number): string {
  return `${Math.round(stretch * 100)}%`;
}

/**
 * Writes the one-line summary of the frame's star shapes.
 *
 * @param measurement The measured shapes.
 * @returns For example "Median elongation 8% toward 40°; width 2.3 px center, 2.6 px corners", or null when
 *   nothing was measured.
 */
export function describeFrameShapes(measurement: StarShapesMeasurement): string | null {
  if (measurement.median_elongation == null) return null;
  const stretch = `Median elongation ${formatStretch(measurement.median_elongation)}`;
  const direction = measurement.direction_degrees != null && measurement.median_elongation >= ROUND_STRETCH_FRACTION
    ? ` toward ${Math.round(measurement.direction_degrees) % 180}°`
    : '';
  const widths: string[] = [];
  if (measurement.center_width_px != null) widths.push(`${measurement.center_width_px.toFixed(1)} px center`);
  if (measurement.corner_width_px != null) widths.push(`${measurement.corner_width_px.toFixed(1)} px corners`);
  const width = widths.length > 0 ? `; width ${widths.join(', ')}` : '';
  return `${stretch}${direction}${width}`;
}

/**
 * Finds the measured star nearest to a position.
 *
 * @param measurement The measured shapes.
 * @param x Column, 1-based.
 * @param y Row, 1-based.
 * @returns The star and its distance in image pixels, or null when no star was measured.
 */
export function findNearestShape(
  measurement: StarShapesMeasurement,
  x: number,
  y: number,
): { shape: StarShape; distancePx: number } | null {
  let best: { shape: StarShape; distancePx: number } | null = null;
  for (const shape of measurement.shapes) {
    const distancePx = Math.hypot(shape.x - x, shape.y - y);
    if (best === null || distancePx < best.distancePx) best = { shape, distancePx };
  }
  return best;
}

/**
 * Compares a clicked star with the frame.
 *
 * @param measurement The measured shapes.
 * @param x Column of the clicked star's center, 1-based.
 * @param y Row of the clicked star's center, 1-based.
 * @returns For example "This star: oval 29%; frame median 8%", or null when there is nothing to compare.
 *   The measured stars are about one in each cell of a grid, so a clicked star is often not one of them; the
 *   line then names the nearest measured star instead.
 */
export function describeStarAgainstFrame(measurement: StarShapesMeasurement, x: number, y: number): string | null {
  const nearest = findNearestShape(measurement, x, y);
  if (nearest === null || measurement.median_elongation == null) return null;
  const { shape, distancePx } = nearest;
  const label = distancePx <= SAME_STAR_DISTANCE_PX ? 'This star' : `Nearest measured star (${Math.round(distancePx)} px away)`;
  return `${label}: ${describeStretch(shape.elongation)} ${formatStretch(shape.elongation)}; frame median ${formatStretch(measurement.median_elongation)}`;
}
