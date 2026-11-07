/**
 * @fileoverview Optional timing marks for the Planetarium's draw loop.
 *
 * The performance-measure (TPM) runner in `build/tpm/` sets
 * `window.__ASTROMETRICS_PERF__ = true` while it pans the sky. Only then do
 * these helpers read the clock and record a `performance.measure` entry, so
 * the runner can tell how long star projection and overlay drawing take. When
 * the flag is not set, each call is one property check and nothing else.
 */

declare global {
  interface Window {
    /** Set to `true` by the performance runner to switch the timing marks on. */
    __ASTROMETRICS_PERF__?: boolean;
  }
}

/**
 * Starts a timing mark.
 *
 * @returns {number | null} The start time in milliseconds, or `null` when timing is off.
 */
export function perfStart(): number | null {
  return typeof window !== 'undefined' && window.__ASTROMETRICS_PERF__ === true ? performance.now() : null;
}

/**
 * Ends a timing mark and records it as a `performance.measure` entry.
 *
 * @param {string} name - The measure name, for example `planetarium:overlay_draw`.
 * @param {number | null} startedAt - The value `perfStart` returned.
 * @returns {void}
 */
export function perfEnd(name: string, startedAt: number | null): void {
  if (startedAt === null) return;
  performance.measure(name, { start: startedAt, end: performance.now() });
}
