/**
 * @file plotHoverLabel.ts
 * @description The look of the hover label (the box that follows the pointer over a plot), taken from the
 * theme tokens so every plot shows it in the same style as the app's menus. Plotly's own default is an
 * orange or trace-colored box, which looks out of place.
 */

/** The parts of a Plotly hover label that the theme sets. */
export interface PlotHoverLabel {
  /** Fill of the box. */
  bgcolor: string;
  /** Edge of the box. */
  bordercolor: string;
  /** Text of the box. */
  font: { color: string; family: string; size: number };
}

/**
 * Reads one theme token from the page.
 *
 * @param name The custom property, for example `--plot-font`.
 * @param fallback Used when the token is not set (a test, or a page without the theme).
 * @returns The token's value.
 */
function readToken(name: string, fallback: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  } catch {
    return fallback;
  }
}

/**
 * Builds the hover label for a plot from the theme: the menu surface for the box, the subtle border
 * for its edge, and the plot font at the app's small size.
 *
 * @returns The hover label setting for a Plotly layout.
 */
export function themedHoverLabel(): PlotHoverLabel {
  const smallSize = parseInt(readToken('--small-font-size', '11px'), 10);
  return {
    bgcolor: readToken('--surface-menu', '#2e2e32'),
    bordercolor: readToken('--border-subtle', 'rgba(255, 255, 255, 0.08)'),
    font: {
      color: readToken('--plot-font', '#ffffff'),
      family: readToken('--font-family-base', 'sans-serif'),
      size: Number.isNaN(smallSize) ? 11 : smallSize,
    },
  };
}
