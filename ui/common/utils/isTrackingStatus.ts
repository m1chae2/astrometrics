/**
 * @fileoverview One rule for reading the mount's tracking status text.
 */

/**
 * Tells whether the mount's status text says it is tracking the sky.
 *
 * The mount reports "Tracking", "Idle" or "Parked". The app's own default
 * text is "Not Tracking". A plain search for the word "tracking" would also
 * match "Not Tracking", so that text is ruled out first.
 *
 * @param {string | null | undefined} statusText - The tracking status text.
 * @return {boolean} True only when the mount is tracking.
 */
export const isTrackingStatus = (statusText: string | null | undefined): boolean => {
    if (typeof statusText !== 'string') return false;
    if (/\bnot\b/i.test(statusText)) return false;
    return /\btracking\b/i.test(statusText);
};
