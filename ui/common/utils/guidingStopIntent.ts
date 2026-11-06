/**
 * @fileoverview Remembers that the user, not a fault, just stopped guiding.
 * The "guiding lost" alert in the status header reads this so that it stays
 * quiet after the user presses Stop Guiding.
 */

/** How long, in milliseconds, a Stop Guiding press explains a guiding stop. */
export const USER_STOP_GUIDING_WINDOW_MS = 120000;

/** When the user last pressed Stop Guiding, in milliseconds since 1970 (0 = never). */
let lastUserStopMs = 0;

/** Records that the user just asked guiding to stop. */
export function markGuidingStoppedByUser(): void {
  lastUserStopMs = Date.now();
}

/**
 * Tells whether the user asked guiding to stop recently.
 * @param nowMs The current time in milliseconds.
 * @return True if a Stop Guiding press is within the window.
 */
export function guidingStopWasUserRequested(nowMs: number = Date.now()): boolean {
  return lastUserStopMs > 0 && nowMs - lastUserStopMs <= USER_STOP_GUIDING_WINDOW_MS;
}

/** Forgets any recorded stop (used by tests). */
export function resetGuidingStopIntent(): void {
  lastUserStopMs = 0;
}
