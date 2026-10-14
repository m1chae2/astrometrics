/**
 * @module durationFormat
 * @fileoverview Turns a length of time in seconds into short display text
 * for the alignment overlay and its details card.
 */

/**
 * Formats a duration in seconds into a compact string like "13m 20s" or "1h 5m".
 *
 * @param {number} seconds - Duration in seconds.
 * @returns {string} Formatted duration.
 */
export function formatDuration(seconds: number): string {
  if (seconds <= 0) return '0s';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins < 60) {
    return `${mins}m ${secs}s`;
  }
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}
