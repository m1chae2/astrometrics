/**
 * @fileoverview Tests of the `astrometrics/selector-ownership` stylelint rule, run through
 * stylelint's own API on small stylesheets.
 */

import { describe, expect, it } from 'vitest';
import stylelint from 'stylelint';

const CONFIG = {
  plugins: ['./build/stylelint/selector-ownership.mjs'],
  rules: { 'astrometrics/selector-ownership': true },
};

/**
 * Lints one stylesheet as if it lived at `codeFilename`.
 * @param {string} code The CSS text.
 * @param {string} codeFilename The path the rule sees.
 * @returns {Promise<string[]>} The warning texts.
 */
async function lint(code, codeFilename) {
  const { results } = await stylelint.lint({ code, codeFilename, config: CONFIG });
  return results[0].warnings.map((warning) => warning.text);
}

describe('selector-ownership', () => {
  it('rejects an owned class in another stylesheet', async () => {
    const warnings = await lint('.btn { width: auto; }', `${process.cwd()}/ui/foo/foo.css`);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('belongs to Button');
  });

  it('rejects a descendant selector, a modifier and a pseudo class', async () => {
    const css = '.panel .btn--tiny {}\n.panel:hover .popover-select__menu {}\n:where(.tabs__tab) {}\n.entry:focus {}';
    expect(await lint(css, `${process.cwd()}/ui/foo/foo.css`)).toHaveLength(4);
  });

  it('allows the owner stylesheet', async () => {
    const warnings = await lint('.btn:hover {} .btn-ghost--square {}', `${process.cwd()}/ui/common/components/Button.css`);
    expect(warnings).toEqual([]);
  });

  it('does not reject similar names, values or comments', async () => {
    const css = '/* .btn is the button */\n.button-row, .badge-count, .entry-list, .tabs-bar { content: ".btn"; }';
    expect(await lint(css, `${process.cwd()}/ui/foo/foo.css`)).toEqual([]);
  });
});
