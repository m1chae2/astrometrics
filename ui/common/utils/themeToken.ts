/**
 * @file themeToken.ts
 * @description Reads a value from the app theme (`ui/common/styles/theme.css`), such as a color
 * or a font size, for code that cannot use CSS variables directly: a canvas layer or a Plotly
 * chart. This is the one place the app reads theme tokens from script. Each value is read once
 * and kept, because the theme does not change while the app runs and a sky layer draws many
 * times a second.
 */

const cachedTokens = new Map<string, string>();

/**
 * Reads a theme token, such as `--plot-accent`.
 *
 * An empty answer (the stylesheet has not loaded yet, or a test that loads no stylesheet) is not
 * kept, so the next call reads the token again.
 *
 * @param name The token name, with its leading `--`.
 * @param fallback The value to use when the token has none.
 * @returns The token's value, or `fallback`.
 */
export function readThemeToken(name: string, fallback: string): string {
    const cached = cachedTokens.get(name);
    if (cached) return cached;
    let value: string;
    try {
        value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch {
        value = '';
    }
    if (!value) return fallback;
    cachedTokens.set(name, value);
    return value;
}

/**
 * Reads a theme token as a whole number, such as `--small-font-size` (`11px` gives 11).
 *
 * @param name The token name, with its leading `--`.
 * @param fallback The number to use when the token has no value or no number.
 * @returns The number at the start of the token's value, or `fallback`.
 */
export function readThemeTokenNumber(name: string, fallback: number): number {
    const parsed = parseInt(readThemeToken(name, ''), 10);
    return Number.isNaN(parsed) ? fallback : parsed;
}

/**
 * Forgets every kept token value, so the next read asks the page again. Tests use it after they
 * set a token.
 *
 * @returns Nothing.
 */
export function clearThemeTokens(): void {
    cachedTokens.clear();
}
