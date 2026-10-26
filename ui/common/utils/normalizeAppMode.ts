/**
 * @fileoverview Turns an old or alternate display name into the current one.
 *
 * Saved settings, jump lists and messages from the backend may still carry a
 * name a display used before it was renamed. Every place that reads a mode
 * passes it through `normalizeAppMode` first.
 */

/**
 * Gives the current name of a display.
 * @param mode A display name, current or old.
 * @return The current name. A name that is not an old one is returned unchanged.
 */
export function normalizeAppMode(mode: string): string {
  if (mode === 'Astronomy Display') return 'Astronomy Manager';
  if (mode === 'Image Processing Display') return 'Image Processing';
  // This branch briefly split Observation Manager into these two displays.
  if (mode === 'Observation Planner' || mode === 'Observation Execution') return 'Observation Manager';
  return mode;
}
