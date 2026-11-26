/**
 * @module skyTargetFilter
 * @fileoverview Decides which library targets the sky map draws, so the target
 * circles and the Images layer follow the target list's filters ("Show" and
 * "Above horizon only"). The search text and the sort order do not change the sky.
 */

import type { StackSkyPicture } from '../../common/types/planetariumTypes';
import type { HorizonCheck } from '../hooks/useTargetsBelowHorizon';
import { normalizeTargetName } from '../hooks/useTargetsBelowHorizon';

/**
 * Works out which library targets the sky may show.
 *
 * @param {string[]} libraryTargetNames - Every library target the sky map knows.
 * @param {string[] | null} shownByFilter - Names the "Show" filter lets through, or null when it is off.
 * @param {HorizonCheck | null} horizonCheck - The horizon check, or null when "Above horizon only" is off
 *   (or the site is not known yet).
 * @returns {Set<string> | null} Normalized names (see `normalizeTargetName`) to show, or null when
 *   nothing is filtered and every target is shown.
 */
export const buildSkyVisibleTargetKeys = (
  libraryTargetNames: string[],
  shownByFilter: string[] | null,
  horizonCheck: HorizonCheck | null,
): Set<string> | null => {
  if (shownByFilter === null && horizonCheck === null) return null;
  const allowedByFilter = shownByFilter === null ? null : new Set(shownByFilter.map(normalizeTargetName));
  const visible = new Set<string>();
  for (const name of libraryTargetNames) {
    const key = normalizeTargetName(name);
    if (allowedByFilter && !allowedByFilter.has(key)) continue;
    if (horizonCheck && (!horizonCheck.checked.has(key) || horizonCheck.belowHorizon.has(key))) continue;
    visible.add(key);
  }
  return visible;
};

/**
 * Tells whether the sky draws one target.
 *
 * @param {string} targetId - The target's name.
 * @param {Set<string> | null} visibleKeys - From `buildSkyVisibleTargetKeys`; null shows everything.
 * @param {Set<string>} libraryKeys - Normalized names of the library targets. Other markers
 *   (catalog objects that are not in the library) are not filtered.
 * @param {string[]} alwaysShownIds - Targets drawn even when filtered out, such as the selected one.
 * @returns {boolean} True when the target is drawn.
 */
export const isSkyTargetShown = (
  targetId: string,
  visibleKeys: Set<string> | null,
  libraryKeys: Set<string>,
  alwaysShownIds: string[],
): boolean => {
  if (visibleKeys === null) return true;
  const key = normalizeTargetName(targetId);
  if (!libraryKeys.has(key) || visibleKeys.has(key)) return true;
  return alwaysShownIds.some((id) => id !== '' && normalizeTargetName(id) === key);
};

/**
 * Keeps the stack pictures of the targets the sky draws. A picture that is left
 * out is never loaded, because pictures load only when they are drawn.
 *
 * @param {StackSkyPicture[]} pictures - Every stack picture.
 * @param {Set<string> | null} visibleKeys - From `buildSkyVisibleTargetKeys`; null keeps every picture.
 * @param {Set<string>} libraryKeys - Normalized names of the library targets.
 * @param {string[]} alwaysShownIds - Targets whose picture stays, such as the selected one.
 * @returns {StackSkyPicture[]} The pictures to draw.
 */
export const filterSkyPictures = (
  pictures: StackSkyPicture[],
  visibleKeys: Set<string> | null,
  libraryKeys: Set<string>,
  alwaysShownIds: string[],
): StackSkyPicture[] => {
  if (visibleKeys === null) return pictures;
  return pictures.filter((picture) => isSkyTargetShown(picture.targetId, visibleKeys, libraryKeys, alwaysShownIds));
};
