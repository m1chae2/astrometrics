/**
 * @fileoverview Stylelint rule `astrometrics/selector-ownership`.
 *
 * Each shared control in `ui/common/components/` owns its CSS classes. Only its own stylesheet may
 * write a selector that names one of them. Any other stylesheet that does (`.btn`, or a
 * descendant selector like `.panel .btn`) is an error. The fix is a prop or variant on the
 * component, not a per-screen restyle.
 *
 * The table below is the single list of owned classes. Add a row when a new shared control
 * gets a stylesheet.
 */

import stylelint from 'stylelint';

const { createPlugin, utils: { report, ruleMessages, validateOptions } } = stylelint;

export const ruleName = 'astrometrics/selector-ownership';

export const messages = ruleMessages(ruleName, {
  /**
   * Builds the error text for one forbidden selector.
   * @param {string} className The owned class that was targeted.
   * @param {string} component The component that owns it.
   * @param {string} ownerFile The one stylesheet that may style it.
   * @returns {string} The message.
   */
  rejected: (className, component, ownerFile) =>
    `".${className}" belongs to ${component}. Do not style it here; use a prop or variant of ${component} ` +
    `(change its look only in ${ownerFile}).`,
});

/**
 * Owned classes. `match` tests a class name (no dot); `ownerFile` is the only stylesheet that may use it.
 */
export const OWNERSHIP = [
  { component: 'Button', ownerFile: 'ui/common/components/Button.css',
    match: (name) => /^btn(-ghost)?($|-|--|__)/.test(name) },
  { component: 'Tabs', ownerFile: 'ui/common/components/sharedComponents.css',
    match: (name) => /^tabs($|--|__)/.test(name) },
  { component: 'Badge and StatusChip', ownerFile: 'ui/common/components/Badge.css',
    match: (name) => /^(badge|status-chip)($|--|__)/.test(name) },
  { component: 'PopoverSelect', ownerFile: 'ui/common/components/popoverSelect.css',
    match: (name) => /^popover-select($|--|__)/.test(name) },
  { component: 'TextInput and NumberInput', ownerFile: 'ui/common/components/TextInput.css',
    match: (name) => /^entry($|--|__)/.test(name) },
  { component: 'Skeleton, PanelBody and UpdatingBar', ownerFile: 'ui/common/components/Skeleton.css',
    match: (name) => /^(skeleton|panel-body|updating-bar)($|--|__)/.test(name) },
  { component: 'Checkbox', ownerFile: 'ui/common/components/Checkbox.css',
    match: (name) => /^checkbox($|--|__)/.test(name) },
];

/**
 * Normalizes a file path to forward slashes, relative to the repository when it is inside it.
 * @param {string} filePath Absolute or relative path from stylelint.
 * @returns {string} The path to compare with `ownerFile`.
 */
function repositoryPath(filePath) {
  const normalized = filePath.replaceAll('\\', '/');
  const index = normalized.lastIndexOf('/ui/');
  return index >= 0 ? normalized.slice(index + 1) : normalized;
}

/**
 * The rule function stylelint calls once per stylesheet.
 * @param {boolean} primary `true` turns the rule on.
 * @returns {Function} The checker for one parsed stylesheet.
 */
function rule(primary) {
  return (root, result) => {
    if (!validateOptions(result, ruleName, { actual: primary, possible: [true] })) return;
    const file = repositoryPath(root.source?.input.file ?? '');
    root.walkRules((cssRule) => {
      // Keyframe steps (from, to, 50%) hold no class names.
      for (const [, name] of cssRule.selector.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) {
        const owner = OWNERSHIP.find((entry) => entry.match(name));
        if (owner && owner.ownerFile !== file) {
          report({
            result, ruleName, node: cssRule,
            message: messages.rejected(name, owner.component, owner.ownerFile),
            word: `.${name}`,
          });
        }
      }
    });
  };
}

rule.ruleName = ruleName;
rule.messages = messages;

export default createPlugin(ruleName, rule);
