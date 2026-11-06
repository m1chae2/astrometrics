/**
 * @fileoverview Stylelint settings for the front end (`ui/`).
 *
 * The rules keep colors, font sizes and corner radii in one place,
 * `ui/common/styles/theme.css`. Every other stylesheet must use a token from
 * that file (for example `var(--accent-blue)`, `var(--small-font-size)` or
 * `var(--radius-md)`).
 *
 * Old stylesheets that still hold raw values are listed in `LEGACY_STYLESHEETS`.
 * That list may only shrink: clean a file, then delete its line. Never add a
 * file to it. A new or changed stylesheet must pass without an entry.
 *
 * The list exempts a file from the value rules only. Selector ownership
 * (`build/stylelint/selector-ownership.mjs`) applies to every stylesheet: the
 * shared controls in `ui/common/components/` are styled only in their own CSS.
 */

/**
 * Stylesheets that predate the token rules and still hold raw colors, font
 * sizes or radii. Empty now: every stylesheet is checked. Never add an entry.
 */
const LEGACY_STYLESHEETS = [];

/** Values that are never a raw design value, so the strict-value rule lets them through. */
const NEUTRAL_VALUES = ['inherit', 'initial', 'unset', 'revert', '0', 'none', 'transparent', 'currentColor', '50%', '100%'];

/** The value rules, switched off for the legacy stylesheets. */
const LEGACY_RULE_OFFS = {
  'color-no-hex': null,
  'color-named': null,
  'function-disallowed-list': null,
  'scale-unlimited/declaration-strict-value': null,
};

export default {
  files: ['ui/**/*.css'],
  ignoreFiles: ['**/node_modules/**', 'ui/coverage/**', 'ui/common/styles/theme.css'],
  plugins: ['stylelint-declaration-strict-value', './build/stylelint/selector-ownership.mjs'],
  overrides: [{ files: LEGACY_STYLESHEETS, rules: LEGACY_RULE_OFFS }],
  rules: {
    // Shared controls are styled only in their own component stylesheet.
    'astrometrics/selector-ownership': true,
    // No raw colors: hex, named, or rgb()/hsl() functions.
    'color-no-hex': true,
    'color-named': 'never',
    'function-disallowed-list': ['rgb', 'rgba', 'hsl', 'hsla', 'hwb', 'lab', 'lch', 'oklab', 'oklch'],
    // No raw font sizes or corner radii: they must be a var(--token).
    'scale-unlimited/declaration-strict-value': [
      ['font-size', '/^border(-[a-z]+)*-radius$/'],
      { ignoreValues: NEUTRAL_VALUES, ignoreFunctions: false, disableFix: true },
    ],
  },
};
