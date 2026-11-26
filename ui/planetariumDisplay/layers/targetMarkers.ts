/**
 * @module targetMarkers
 * @fileoverview Decides which marker the Planetarium draws for a library target, and where.
 *
 * The rules:
 * - A target whose stack picture is drawn (the Images layer) gets no marker: the picture
 *   shows where it is. When it is the selected target, a thin outline of the picture's
 *   edge marks it. The outline is the picture's sky grid edge sent through the map's own
 *   projection, so it bends and turns with the picture.
 * - A target with a catalog shape (major axis, minor axis and position angle) gets an
 *   ellipse of that shape, turned to the right direction on the sky. A faint one when it is
 *   not selected, a bright, thicker one when it is. An ellipse too small to see at this zoom
 *   falls back to the small ring (not selected) or is grown to the smallest selected size.
 * - Any other target gets a small, faint ring of fixed screen size when it is not selected,
 *   and four corner brackets around the object's size (see `targetExtentDegrees`) when it is.
 *
 * These functions only work out the shapes; `TargetOverlay` draws them.
 */

import type { PlanetariumTarget, StackSkyPicture } from '../../common/types/planetariumTypes';
import type { ProjectionContext } from './overlayTypes';
import { targetExtentDegrees } from './overlayShared';
import { projectGrid } from './StackPictureOverlay';

/** Smallest size of a selected target's brackets or ellipse, in pixels, so it shows at any zoom. */
export const TARGET_MARKER_MIN_PX = 16.0;

/** Radius of the small ring drawn on a target that is not selected, in pixels. */
export const SMALL_MARKER_RADIUS_PX = 4.0;

/** Length of each corner bracket's arms, as a share of the bracket box's side. */
const BRACKET_ARM_FRACTION = 0.25;

/** Shortest arm of a corner bracket, in pixels. */
const BRACKET_ARM_MIN_PX = 4.0;

/**
 * Largest step, in degrees, taken toward the position angle to find which way the ellipse
 * points on screen. A short step keeps the direction right where the projection bends.
 */
const ORIENTATION_STEP_MAX_DEG = 0.5;

/** A point on the canvas, or null when it is toward the back of the sky. */
export type ScreenPoint = { x: number; y: number } | null;

/** The marker to draw for one target. */
export type TargetMarker =
  | { kind: 'none' }
  | { kind: 'footprint'; outline: ScreenPoint[] }
  | { kind: 'ring'; x: number; y: number; radiusPx: number }
  | { kind: 'brackets'; x: number; y: number; halfSizePx: number; armPx: number }
  | {
      kind: 'ellipse';
      x: number;
      y: number;
      semiMajorPx: number;
      semiMinorPx: number;
      /** Turn of the major axis from the canvas x axis, in radians (clockwise, as the canvas turns). */
      rotationRad: number;
    };

/** What the marker choice needs to know about one target and the view. */
export interface TargetMarkerInput {
  target: PlanetariumTarget;
  isSelected: boolean;
  /** The target's stack picture when the Images layer draws it, else null. */
  shownPicture: StackSkyPicture | null;
  projectCoords: ProjectionContext['projectCoords'];
  /** Canvas pixels per degree at the current zoom. */
  pixelsPerDeg: number;
  width: number;
  height: number;
}

/**
 * The outline of a picture's edge on the canvas: the outer points of its sky grid, in order
 * around it (top row, right column, bottom row back, left column up), projected like the
 * picture itself.
 *
 * @param {StackSkyPicture} picture The picture and its sky grid.
 * @param {ProjectionContext['projectCoords']} projectCoords The sky map's projection.
 * @param {number} [width] The canvas width, to judge how far out a point may be.
 * @param {number} [height] The canvas height.
 * @returns {ScreenPoint[]} The edge points; the last one is the first again, so the path closes.
 *   A point toward the back of the sky is null, which breaks the line there.
 */
export function footprintOutline(
  picture: StackSkyPicture,
  projectCoords: ProjectionContext['projectCoords'],
  width: number = Infinity,
  height: number = Infinity,
): ScreenPoint[] {
  const size = picture.gridSize;
  if (size < 2 || picture.grid.length < size * size) return [];
  const screen = projectGrid(picture, projectCoords, width, height);
  const indices: number[] = [];
  for (let column = 0; column < size; column += 1) indices.push(column);
  for (let row = 1; row < size; row += 1) indices.push(row * size + size - 1);
  for (let column = size - 2; column >= 0; column -= 1) indices.push((size - 1) * size + column);
  for (let row = size - 2; row >= 0; row -= 1) indices.push(row * size);
  return indices.map((index) => screen[index]);
}

/**
 * The sky point a given angle away from a starting point, in a given direction.
 *
 * @param {number} raDeg Starting right ascension, in degrees.
 * @param {number} decDeg Starting declination, in degrees.
 * @param {number} positionAngleDeg Direction, in degrees east of north.
 * @param {number} distanceDeg How far to go, in degrees.
 * @returns {{ ra: number; dec: number }} The point reached, in degrees.
 */
export function offsetOnSky(
  raDeg: number,
  decDeg: number,
  positionAngleDeg: number,
  distanceDeg: number,
): { ra: number; dec: number } {
  const toRad = Math.PI / 180;
  const dec = decDeg * toRad;
  const angle = positionAngleDeg * toRad;
  const distance = distanceDeg * toRad;
  const sinDec2 = Math.sin(dec) * Math.cos(distance) + Math.cos(dec) * Math.sin(distance) * Math.cos(angle);
  const dec2 = Math.asin(Math.max(-1, Math.min(1, sinDec2)));
  const raStep = Math.atan2(
    Math.sin(angle) * Math.sin(distance) * Math.cos(dec),
    Math.cos(distance) - Math.sin(dec) * sinDec2,
  );
  return { ra: (((raDeg + raStep / toRad) % 360) + 360) % 360, dec: dec2 / toRad };
}

/**
 * Reads a target's catalog shape, when it has all three parts.
 *
 * @param {PlanetariumTarget} target The target.
 * @returns {{ majorDeg: number; minorDeg: number; positionAngleDeg: number } | null} The axes
 *   in degrees and the position angle, or null when any part is missing.
 */
export function catalogShape(
  target: PlanetariumTarget,
): { majorDeg: number; minorDeg: number; positionAngleDeg: number } | null {
  const { majorAxisArcmin: major, minorAxisArcmin: minor, positionAngleDeg: angle } = target;
  const isPositive = (value: number | null | undefined): value is number =>
    typeof value === 'number' && Number.isFinite(value) && value > 0;
  if (!isPositive(major) || !isPositive(minor)) return null;
  if (typeof angle !== 'number' || !Number.isFinite(angle)) return null;
  return { majorDeg: major / 60, minorDeg: Math.min(minor, major) / 60, positionAngleDeg: angle };
}

/**
 * Works out the ellipse of a target's catalog shape on the canvas. The direction comes from
 * projecting a point a short way along the position angle, so it stays right however the
 * sky is turned on screen.
 *
 * @param {TargetMarkerInput} input The target and the view.
 * @param {{ x: number; y: number }} center The target's canvas position.
 * @returns {{ semiMajorPx: number; semiMinorPx: number; rotationRad: number } | null} The
 *   ellipse, or null when the target has no shape or the direction cannot be found.
 */
function shapeEllipse(
  input: TargetMarkerInput,
  center: { x: number; y: number },
): { semiMajorPx: number; semiMinorPx: number; rotationRad: number } | null {
  const shape = catalogShape(input.target);
  if (!shape) return null;
  const step = Math.min(shape.majorDeg / 2, ORIENTATION_STEP_MAX_DEG);
  const along = offsetOnSky(input.target.ra, input.target.dec, shape.positionAngleDeg, step);
  const point = input.projectCoords(along.ra, along.dec);
  const deltaX = point.x - center.x;
  const deltaY = point.y - center.y;
  const length = Math.hypot(deltaX, deltaY);
  if (!Number.isFinite(length) || length === 0) return null;
  // Pixels per degree right here, which can differ from the view's center.
  const localScale = length / step;
  return {
    semiMajorPx: (shape.majorDeg / 2) * localScale,
    semiMinorPx: (shape.minorDeg / 2) * localScale,
    rotationRad: Math.atan2(deltaY, deltaX),
  };
}

/**
 * Chooses the marker for one target (see the module notes for the rules).
 *
 * @param {TargetMarkerInput} input The target, whether it is selected, its drawn picture and the view.
 * @returns {TargetMarker} What to draw.
 */
export function chooseTargetMarker(input: TargetMarkerInput): TargetMarker {
  if (input.shownPicture) {
    if (!input.isSelected) return { kind: 'none' };
    const outline = footprintOutline(input.shownPicture, input.projectCoords, input.width, input.height);
    return outline.some(Boolean) ? { kind: 'footprint', outline } : { kind: 'none' };
  }

  const point = input.projectCoords(input.target.ra, input.target.dec);
  if (!point.visible) return { kind: 'none' };
  const center = { x: point.x, y: point.y };

  const ellipse = shapeEllipse(input, center);
  if (ellipse) {
    if (input.isSelected) {
      const grow = Math.max(1, TARGET_MARKER_MIN_PX / (2 * ellipse.semiMajorPx));
      return {
        kind: 'ellipse',
        ...center,
        semiMajorPx: ellipse.semiMajorPx * grow,
        semiMinorPx: ellipse.semiMinorPx * grow,
        rotationRad: ellipse.rotationRad,
      };
    }
    if (ellipse.semiMajorPx > SMALL_MARKER_RADIUS_PX) return { kind: 'ellipse', ...center, ...ellipse };
    return { kind: 'ring', ...center, radiusPx: SMALL_MARKER_RADIUS_PX };
  }

  if (!input.isSelected) return { kind: 'ring', ...center, radiusPx: SMALL_MARKER_RADIUS_PX };
  const sizePx = Math.max(TARGET_MARKER_MIN_PX, targetExtentDegrees(input.target) * input.pixelsPerDeg);
  return {
    kind: 'brackets',
    ...center,
    halfSizePx: sizePx / 2,
    armPx: Math.max(BRACKET_ARM_MIN_PX, sizePx * BRACKET_ARM_FRACTION),
  };
}
