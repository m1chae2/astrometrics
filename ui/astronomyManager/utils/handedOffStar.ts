/**
 * @file handedOffStar.ts
 * @description Reads a star another screen asks the Astronomy Manager to open, and picks the target to show it in.
 */

/** What another screen sends to open a star: just its id, or its id and the target the person came from. */
export type HandedOffStar = string | { starId?: string; targetId?: string };

/**
 * Reads the star id and the target the person came from out of a hand-off.
 *
 * @param payload The hand-off payload: a star id, or an object with `starId` and `targetId`.
 * @returns The star id (empty when none) and the target the person came from, if known.
 */
export const readHandedOffStar = (payload: HandedOffStar | null | undefined): { starId: string; targetId?: string } => {
  if (typeof payload === 'string') return { starId: payload };
  return { starId: payload?.starId ?? '', targetId: payload?.targetId || undefined };
};

/**
 * Picks the target the Targets list should show for a star that was just opened from another screen.
 *
 * The target the person came from wins when the star belongs to it. Otherwise the target already
 * selected stays when the star belongs to it, and otherwise the star's first target is used.
 *
 * @param starTargetIds The targets in the star's record.
 * @param selectedTarget The target selected now, or an empty string.
 * @param cameFromTarget The target the person came from, if the hand-off named one.
 * @returns The target to select, or null when the star has no target (leave the list as it is).
 */
export const chooseTargetForStar = (
  starTargetIds: readonly string[],
  selectedTarget: string,
  cameFromTarget?: string
): string | null => {
  if (starTargetIds.length === 0) return null;
  if (cameFromTarget && starTargetIds.includes(cameFromTarget)) return cameFromTarget;
  if (starTargetIds.includes(selectedTarget)) return selectedTarget;
  return starTargetIds[0];
};
