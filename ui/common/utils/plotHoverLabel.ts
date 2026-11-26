/**
 * @file plotHoverLabel.ts
 * @description The look of the hover label (the box that follows the pointer over a plot), taken from the
 * theme tokens so every plot shows it in the same style as the app's menus. Plotly's own default is an
 * orange or trace-colored box, which looks out of place.
 */
import { readThemeToken, readThemeTokenNumber } from './themeToken';

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
 * Builds the hover label for a plot from the theme: the menu surface for the box, the subtle border
 * for its edge, and the plot font at the app's small size.
 *
 * @returns The hover label setting for a Plotly layout.
 */
export function themedHoverLabel(): PlotHoverLabel {
  return {
    bgcolor: readThemeToken('--surface-menu', '#2e2e32'),
    bordercolor: readThemeToken('--border-subtle', 'rgba(255, 255, 255, 0.08)'),
    font: {
      color: readThemeToken('--plot-font', '#ffffff'),
      family: readThemeToken('--font-family-base', 'sans-serif'),
      size: readThemeTokenNumber('--small-font-size', 11),
    },
  };
}
