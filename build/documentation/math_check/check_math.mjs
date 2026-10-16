// =============================================================================
// Purpose: Find equations on the built docs site that MathJax cannot draw.
//
// Sphinx copies each equation into the HTML page and the reader's browser
// draws it with MathJax. A mistake in an equation (an unknown command, a
// \tag where MathJax forbids it, an unbalanced brace) therefore never fails
// the Sphinx build: the page simply shows red error text. This script reads
// every built HTML page, runs each equation through the same MathJax version
// the site loads (mathjax_path in documentation/conf.py), with the same TeX
// packages, and lists every equation that fails.
//
// Usage: node build/documentation/math_check/check_math.mjs [html folder]
//   The folder defaults to documentation/_build/html.
// Needs: npm ci --prefix build/documentation/math_check
// =============================================================================

import {readdirSync, readFileSync} from 'node:fs';
import {join, relative} from 'node:path';

import {mathjax} from '@mathjax/src/js/mathjax.js';
import {TeX} from '@mathjax/src/js/input/tex.js';
import {liteAdaptor} from '@mathjax/src/js/adaptors/liteAdaptor.js';
import {RegisterHTMLHandler} from '@mathjax/src/js/handlers/html.js';
import {STATE} from '@mathjax/src/js/core/MathItem.js';
import '@mathjax/src/js/input/tex/base/BaseConfiguration.js';
import '@mathjax/src/js/input/tex/ams/AmsConfiguration.js';
import '@mathjax/src/js/input/tex/newcommand/NewcommandConfiguration.js';
import '@mathjax/src/js/input/tex/textmacros/TextMacrosConfiguration.js';
import '@mathjax/src/js/input/tex/configmacros/ConfigMacrosConfiguration.js';
import '@mathjax/src/js/input/tex/action/ActionConfiguration.js';
import '@mathjax/src/js/input/tex/amscd/AmsCdConfiguration.js';
import '@mathjax/src/js/input/tex/bbox/BboxConfiguration.js';
import '@mathjax/src/js/input/tex/boldsymbol/BoldsymbolConfiguration.js';
import '@mathjax/src/js/input/tex/braket/BraketConfiguration.js';
import '@mathjax/src/js/input/tex/cancel/CancelConfiguration.js';
import '@mathjax/src/js/input/tex/color/ColorConfiguration.js';
import '@mathjax/src/js/input/tex/enclose/EncloseConfiguration.js';
import '@mathjax/src/js/input/tex/extpfeil/ExtpfeilConfiguration.js';
import '@mathjax/src/js/input/tex/html/HtmlConfiguration.js';
import '@mathjax/src/js/input/tex/mhchem/MhchemConfiguration.js';
import '@mathjax/src/js/input/tex/unicode/UnicodeConfiguration.js';
import '@mathjax/src/js/input/tex/verb/VerbConfiguration.js';

// The browser's TeX packages: the ones MathJax's tex-mml-chtml component
// loads at start, plus the ones it loads on first use (autoload). The
// browser's noundefined package, which draws an unknown command in red
// instead of failing, is left out so that unknown commands are reported.
// bussproofs (logic proof trees) is left out because it needs a drawing
// engine to measure boxes, and the docs have no proof trees.
const PACKAGES = [
  'base', 'ams', 'newcommand', 'textmacros', 'configmacros',
  'action', 'amscd', 'bbox', 'boldsymbol', 'braket', 'cancel',
  'color', 'enclose', 'extpfeil', 'html', 'mhchem', 'unicode', 'verb',
];
const MATH_ELEMENT = /<(div|span) class="math notranslate nohighlight"[^>]*>([\s\S]*?)<\/\1>/g;
const ENTITIES = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'"};

/**
 * List the HTML files under a folder.
 * @param {string} folder - The folder to search.
 * @returns {string[]} The paths of every .html file, at any depth.
 */
function htmlFiles(folder) {
  return readdirSync(folder, {withFileTypes: true}).flatMap((entry) => {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) return entry.name.startsWith('_') ? [] : htmlFiles(path);
    return entry.name.endsWith('.html') ? [path] : [];
  });
}

/**
 * Pull the TeX source of each equation out of one built page.
 * @param {string} html - The page's HTML.
 * @returns {{tex: string, display: boolean}[]} Each equation and whether it is a display equation.
 */
function equations(html) {
  const found = [];
  for (const [, tag, inner] of html.matchAll(MATH_ELEMENT)) {
    const text = inner
      .replace(/<span class="eqno">[\s\S]*?<\/span>/g, '')
      .replace(/<[^>]+>/g, '')
      .replace(/&(amp|lt|gt|quot|#39);/g, (entity) => ENTITIES[entity])
      .trim();
    const delimited = text.match(/^\\[[(]([\s\S]*)\\[\])]$/);
    found.push({tex: delimited ? delimited[1] : text, display: tag === 'div'});
  }
  return found;
}

const folder = process.argv[2] ?? 'documentation/_build/html';
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const tex = new TeX({
  packages: PACKAGES,
  formatError: (_jax, error) => {
    throw error;
  },
});
const document = mathjax.document('', {InputJax: tex});

let checked = 0;
const problems = [];
for (const file of htmlFiles(folder)) {
  for (const {tex: source, display} of equations(readFileSync(file, 'utf8'))) {
    checked += 1;
    try {
      document.convert(source, {display, end: STATE.CONVERT});
    } catch (error) {
      const excerpt = source.replace(/\s+/g, ' ').slice(0, 100);
      problems.push(`${relative(folder, file)}: ${error.message}\n    ${excerpt}`);
    }
  }
}

if (problems.length) {
  console.error(`MathJax cannot draw ${problems.length} of ${checked} equations:`);
  for (const problem of problems) console.error(`  ${problem}`);
  process.exit(1);
}
console.log(`MathJax draws all ${checked} equations.`);
