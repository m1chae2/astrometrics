/**
 * @file stackFormatting.ts
 * @description Small helpers that write stack numbers as plain text with units. The Stack Summary
 * block and the panels opened from it share them.
 */

/**
 * Writes an exposure length with its unit.
 * @param seconds The exposure, in seconds.
 * @return Text such as `30 s` or `0.5 s`.
 */
export const formatExposureSeconds = (seconds: number): string =>
    `${Number.isInteger(seconds) ? seconds.toFixed(0) : seconds.toFixed(2).replace(/0+$/, '')} s`;
