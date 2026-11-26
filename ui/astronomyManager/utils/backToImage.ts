/**
 * @file backToImage.ts
 * @description Decides what the Astronomy Manager's "Back to image" button does. When the star on screen is the
 * one the person opened from the Image Processing picture, under the same target, the button puts that saved
 * view back. Otherwise (the person picked another star, or another target) it opens the target the star
 * belongs to now, on its main stack, with that star selected and the view zoomed in on it.
 */
import type { SavedImageView, StarFocusHandoff } from '../../common/utils/imageViewStore';

/** What "Back to image" should do. */
export type BackToImagePlan =
  | { kind: 'restore' }
  | { kind: 'focus'; handoff: StarFocusHandoff };

/**
 * Picks the target to open for a star: the target selected in the Targets list when the star belongs to it,
 * otherwise the star's first target, otherwise the list's target.
 *
 * @param starTargetIds The targets in the star's record.
 * @param listTarget The target selected in the Astronomy Manager's Targets list, or an empty string.
 * @returns The target, or null when there is none.
 */
export function targetForStar(starTargetIds: readonly string[], listTarget: string): string | null {
  if (listTarget && starTargetIds.includes(listTarget)) return listTarget;
  if (starTargetIds.length > 0) return starTargetIds[0];
  return listTarget || null;
}

/**
 * Plans the "Back to image" button.
 *
 * @param saved The saved Image Processing view, or null.
 * @param shownStarId The library id of the star on screen, or an empty string.
 * @param shownStarName The star's name, for the note Image Processing shows when its stack does not name it.
 * @param starTargetIds The targets in the star's record.
 * @param listTarget The target selected in the Targets list, or an empty string.
 * @returns Put the saved view back, or open the star on its target's main stack.
 */
export function planBackToImage(
  saved: SavedImageView | null,
  shownStarId: string,
  shownStarName: string,
  starTargetIds: readonly string[],
  listTarget: string,
): BackToImagePlan {
  const targetId = targetForStar(starTargetIds, listTarget);
  if (!shownStarId || !targetId) return { kind: 'restore' };
  const sameStar = Boolean(saved?.arrivedFromRecord) && saved?.arrivedFromRecord === shownStarId;
  if (sameStar && saved?.target === targetId) return { kind: 'restore' };
  return {
    kind: 'focus',
    handoff: { targetId, enableAstrometry: true, focusLibraryStarId: shownStarId, focusStarName: shownStarName },
  };
}
