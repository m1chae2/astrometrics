/**
 * @module themeColor
 * @fileoverview Reads a color token from the app theme (`ui/common/styles/theme.css`) for
 * drawing on a canvas. A canvas cannot use CSS variables directly, so a layer asks for the
 * token's value here. Each value is read once and kept, because the theme does not change
 * while the app runs and a layer draws many times a second.
 */

/** The color used when a token has no value, as in tests, which load no stylesheet. */
export const MISSING_TOKEN_COLOR = '#ffffff';

const cachedColors = new Map<string, string>();

/**
 * Reads the color of a theme token, such as `--sky-target-marker`.
 *
 * An empty answer (the stylesheet has not loaded yet) is not kept, so the next call reads
 * the token again.
 *
 * @param {string} token The token name, with its leading `--`.
 * @returns {string} The token's color text, or {@link MISSING_TOKEN_COLOR} when it has none.
 */
export function readThemeColor(token: string): string {
  const cached = cachedColors.get(token);
  if (cached) return cached;
  let value: string;
  try {
    value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  } catch {
    value = '';
  }
  if (!value) return MISSING_TOKEN_COLOR;
  cachedColors.set(token, value);
  return value;
}

/**
 * Forgets every kept token value, so the next read asks the page again. Tests use it.
 *
 * @returns {void}
 */
export function clearThemeColors(): void {
  cachedColors.clear();
}
