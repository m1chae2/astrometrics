/**
 * @module overlayShared
 * @fileoverview Constants and helpers shared across multiple overlay layers.
 */

import { PlanetariumTarget } from '../../common/types/planetariumTypes';

/** Default target field-of-view used when the metadata string is absent, in arcminutes. */
export const TARGET_DEFAULT_FOV_ARCMIN = 18;

/**
 * Parses a target's field-of-view metadata string into degrees, falling back to
 * {@link TARGET_DEFAULT_FOV_ARCMIN} when the string is absent or unparsable.
 *
 * @param {PlanetariumTarget} target - The target whose fieldOfView string to parse.
 * @returns {number} Field of view in degrees.
 */
export function parseTargetFovDegrees(target: PlanetariumTarget): number {
  const fieldOfViewText = target.fieldOfView || '';
  const fieldOfViewArcmin =
    parseFloat(fieldOfViewText.replace(/[′']/g, '')) || TARGET_DEFAULT_FOV_ARCMIN;
  return fieldOfViewArcmin / 60.0;
}

/**
 * The size of a target on the sky: its catalog major axis when the library has one, else its
 * field-of-view string (see {@link parseTargetFovDegrees}). The target markers are drawn at
 * this size and the click test uses it, so a click anywhere on a marker picks its target.
 *
 * @param {PlanetariumTarget} target - The target.
 * @returns {number} The size in degrees.
 */
export function targetExtentDegrees(target: PlanetariumTarget): number {
  const majorAxisArcmin = target.majorAxisArcmin;
  if (typeof majorAxisArcmin === 'number' && Number.isFinite(majorAxisArcmin) && majorAxisArcmin > 0) {
    return majorAxisArcmin / 60.0;
  }
  return parseTargetFovDegrees(target);
}

/**
 * Determines whether any part of the ground could be visible in the current
 * viewport — i.e. whether the bottom edge of the field of view dips below the
 * horizon. When the whole viewport sits above the horizon there is no ground
 * to draw or occlude anything with.
 *
 * @param {number} centerAlt - Viewport center altitude in degrees.
 * @param {number} fov - Current field of view in degrees.
 * @returns {boolean} True if the viewport's lower edge is below the horizon.
 */
export function viewportDipsBelowHorizon(centerAlt: number, fov: number): boolean {
  return centerAlt - fov / 2 < 0;
}

/**
 * Determines whether a source at the given altitude is hidden beneath the ground
 * overlay. The ground only actually occludes anything when the viewport itself
 * dips below the horizon (see {@link viewportDipsBelowHorizon}) — a source below
 * the horizon in a viewport that sits entirely above it is still visibly rendered
 * and must remain selectable.
 *
 * @param {boolean} showEnvironment - Whether the ground/horizon overlay is enabled.
 * @param {number} centerAlt - Viewport center altitude in degrees.
 * @param {number} fov - Current field of view in degrees.
 * @param {number} alt - Altitude of the candidate source in degrees.
 * @returns {boolean} True if the source is hidden beneath the ground overlay.
 */
export function shouldOccludeBelowHorizon(
  showEnvironment: boolean,
  centerAlt: number,
  fov: number,
  alt: number,
): boolean {
  return showEnvironment && viewportDipsBelowHorizon(centerAlt, fov) && alt < 0;
}
