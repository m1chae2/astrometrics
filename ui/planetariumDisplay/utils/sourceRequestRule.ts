/**
 * @fileoverview Rule for skipping a source request that an earlier one already answers.
 *
 * The sky map draws only stars inside the view and brighter than its limiting
 * magnitude, so sources fetched for a larger, deeper query still serve a smaller,
 * shallower one. Zooming in therefore needs no new request.
 */

import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { angularSeparationDeg } from './catalogSourceCache';

/** What a source request asked for. */
export interface SourceQueryShape {
  ra: number;
  dec: number;
  radius: number;
  /** Faintest magnitude requested; undefined means no limit. */
  limitingMagnitude?: number;
  /** Whether stars without a catalog magnitude were requested. */
  includeUncataloged?: boolean;
}

/**
 * Tells whether the sources fetched for `fetched` also cover the request `wanted`.
 *
 * @param {SourceQueryShape | null} fetched - The last completed request, or null if none.
 * @param {SourceQueryShape} wanted - The request about to be sent.
 * @returns {boolean} True when `wanted` needs no new request.
 */
export function isCoveredByFetched(fetched: SourceQueryShape | null, wanted: SourceQueryShape): boolean {
  if (!fetched) return false;
  if (angularSeparationDeg(wanted.ra, wanted.dec, fetched.ra, fetched.dec) + wanted.radius > fetched.radius) {
    return false;
  }
  if (fetched.limitingMagnitude !== undefined) {
    if (wanted.limitingMagnitude === undefined || wanted.limitingMagnitude > fetched.limitingMagnitude) return false;
  }
  if (wanted.includeUncataloged && !fetched.includeUncataloged) return false;
  return true;
}

/**
 * Tells whether two source lists hold the same stars, so the older array can be kept.
 * Compares length, then the fields the sky map draws and picks by.
 *
 * @param {PlanetariumSource[]} previous - The list currently in use.
 * @param {PlanetariumSource[]} next - A freshly fetched list.
 * @returns {boolean} True when the lists are equal for drawing purposes.
 */
export function sameSources(previous: PlanetariumSource[], next: PlanetariumSource[]): boolean {
  if (previous === next) return true;
  if (previous.length !== next.length) return false;
  for (let index = 0; index < previous.length; index++) {
    const a = previous[index];
    const b = next[index];
    if (
      a.id !== b.id ||
      a.ra !== b.ra ||
      a.dec !== b.dec ||
      a.magnitude !== b.magnitude ||
      a.name !== b.name ||
      a.type !== b.type ||
      a.hasSpectra !== b.hasSpectra ||
      a.hasPhotometry !== b.hasPhotometry ||
      a.aboveHorizon !== b.aboveHorizon ||
      a.stackedImage !== b.stackedImage
    ) {
      return false;
    }
  }
  return true;
}
