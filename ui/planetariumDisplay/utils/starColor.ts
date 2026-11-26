/**
 * @module starColor
 * @fileoverview Picks the color a star with a spectrum is drawn in, in the
 * Planetarium's "By spectral class" mode.
 *
 * Each class has a theme color token (`--star-color-o` and so on, in
 * `ui/common/styles/theme.css`). A star's class comes from our own spectrum's
 * type first, then the catalog type, then the catalog B-V color.
 */

import { spectralClassLetter } from '../../common/utils/starDisplayFormat';

/** The color class for a star with no known type: drawn neutral gray. */
export const NO_COLOR_CLASS = 'none';

/** One entry of the color legend, hottest first. */
export interface StarColorLegendEntry {
  /** The color class ('O' to 'M', 'C', 'W' or 'none'). */
  colorClass: string;
  /** Short label shown in the legend. */
  label: string;
}

/** The color legend: the main classes hottest first, then the rarer ones, then "no type". */
export const STAR_COLOR_LEGEND: readonly StarColorLegendEntry[] = [
  { colorClass: 'O', label: 'O' },
  { colorClass: 'B', label: 'B' },
  { colorClass: 'A', label: 'A' },
  { colorClass: 'F', label: 'F' },
  { colorClass: 'G', label: 'G' },
  { colorClass: 'K', label: 'K' },
  { colorClass: 'M', label: 'M' },
  { colorClass: 'C', label: 'C' },
  { colorClass: 'W', label: 'W' },
  { colorClass: NO_COLOR_CLASS, label: 'No type' },
];

/**
 * B-V color where each main class starts, for a main-sequence star: a star
 * redder (higher) than a class's start and bluer than the next one's is in
 * that class. Standard values: O5 -0.33, B0 -0.30, A0 0.00, F0 0.30,
 * G0 0.58, K0 0.81, M0 1.40 (for example Allen's Astrophysical Quantities).
 * Giants of the same color can be one class off, so this is only the last
 * fallback.
 */
const B_MINUS_V_CLASS_STARTS: ReadonlyArray<[number, string]> = [
  [1.40, 'M'],
  [0.81, 'K'],
  [0.58, 'G'],
  [0.30, 'F'],
  [0.00, 'A'],
  [-0.30, 'B'],
];

/**
 * The main spectral class a B-V color points to.
 *
 * @param {number} bMinusV The star's B-V color.
 * @returns {string} The class letter, 'O' to 'M'.
 */
export function classFromBMinusV(bMinusV: number): string {
  for (const [start, letter] of B_MINUS_V_CLASS_STARTS) {
    if (bMinusV >= start) return letter;
  }
  return 'O';
}

/**
 * The color class of a spectral type text, such as "G2V", "C5:,4", "R" or "WR".
 *
 * Uses the same class letters as the class lists (R and N are carbon stars,
 * so C). S-type stars are cool giants close to the carbon stars, so they get
 * the carbon red too, although the class lists do not group them.
 *
 * @param {string | null | undefined} spectralType The spectral type text.
 * @returns {string} The color class, or an empty string when the type says nothing.
 */
export function colorClassOfType(spectralType: string | null | undefined): string {
  const letter = spectralClassLetter(spectralType);
  if (letter) return letter;
  const first = (spectralType ?? '').trim().charAt(0).toUpperCase();
  return first === 'S' ? 'C' : '';
}

/** What decides a star's color. */
export interface StarColorFacts {
  /** The type our own spectrum matched, empty when none. */
  ownSpectralType?: string | null;
  /** The catalog spectral type, empty when unknown. */
  spectralType?: string | null;
  /** The catalog B-V color, null when unknown. */
  bMinusV?: number | null;
}

/**
 * The color class a star is drawn in: our own spectrum's type, else the
 * catalog type, else the B-V color, else none (gray).
 *
 * @param {StarColorFacts} star The star's types and color.
 * @returns {string} The color class ('O' to 'M', 'C', 'W' or 'none').
 */
export function starColorClass(star: StarColorFacts): string {
  const ownClass = colorClassOfType(star.ownSpectralType);
  if (ownClass) return ownClass;
  const catalogClass = colorClassOfType(star.spectralType);
  if (catalogClass) return catalogClass;
  if (typeof star.bMinusV === 'number' && Number.isFinite(star.bMinusV)) return classFromBMinusV(star.bMinusV);
  return NO_COLOR_CLASS;
}

/**
 * The theme token that holds a color class's color.
 *
 * @param {string} colorClass The color class.
 * @returns {string} The CSS custom property name, for example '--star-color-g'.
 */
export function starColorToken(colorClass: string): string {
  return `--star-color-${colorClass.toLowerCase()}`;
}

/** Red, green and blue, each from 0 to 255. */
export type RgbColor = [number, number, number];

/** White, used for a star with no color class and when a token cannot be read. */
export const WHITE_RGB: RgbColor = [255, 255, 255];

const resolvedColors = new Map<string, RgbColor>();

/**
 * Forgets the colors read from the theme, so the next star reads its token again.
 *
 * The star renderers call this once at the start of each drawn frame, so a changed
 * `--star-color-*` token shows on the next redraw without a reload, while a frame of
 * thousands of stars still reads each token only once.
 *
 * @returns {void}
 */
export function refreshStarColors(): void {
  resolvedColors.clear();
}

/**
 * Reads a `#rrggbb` or `#rgb` color.
 *
 * @param {string} text The color text.
 * @returns {RgbColor | null} The color, or null when the text is not a hex color.
 */
function parseHexColor(text: string): RgbColor | null {
  const hex = text.trim().replace(/^#/, '');
  const full = hex.length === 3 ? hex.split('').map((digit) => digit + digit).join('') : hex;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return [0, 2, 4].map((start) => parseInt(full.slice(start, start + 2), 16)) as RgbColor;
}

/**
 * The drawing color of a color class, read from its theme token once per drawn frame
 * (see `refreshStarColors`).
 *
 * Falls back to white when the token is missing (as in tests, which load no
 * stylesheet), so a star is never invisible.
 *
 * @param {string | undefined} colorClass The color class, or undefined for a star without one.
 * @returns {RgbColor} The color to draw the star in.
 */
export function starColorRgb(colorClass: string | undefined): RgbColor {
  if (!colorClass) return WHITE_RGB;
  const cached = resolvedColors.get(colorClass);
  if (cached) return cached;
  let color: RgbColor | null;
  try {
    color = parseHexColor(getComputedStyle(document.documentElement).getPropertyValue(starColorToken(colorClass)));
  } catch {
    color = null;
  }
  if (!color) return WHITE_RGB;
  resolvedColors.set(colorClass, color);
  return color;
}
