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
 */

/**
 * Stylesheets that predate the token rules and still hold raw colors, font
 * sizes or radii. Remove an entry when the file is cleaned; never add one.
 */
const LEGACY_STYLESHEETS = [
  'ui/App.css',
  'ui/astronomyManager/styles/astronomyManager.css',
  'ui/astronomyManager/styles/astronomyViewer.css',
  'ui/astronomyManager/styles/timeline.css',
  'ui/commandConsole/commandConsole.css',
  'ui/common/components/customSelect.css',
  'ui/common/components/tabView.css',
  'ui/common/fitsViewer/imageViewer/imageViewer.css',
  'ui/common/styles/ConfirmDialog.css',
  'ui/common/styles/addTargetModal.css',
  'ui/common/styles/animations.css',
  'ui/common/styles/button.css',
  'ui/common/styles/emptyState.css',
  'ui/common/styles/entry.css',
  'ui/common/styles/index.css',
  'ui/common/styles/ingestFramesModal.css',
  'ui/common/styles/listCommon.css',
  'ui/common/styles/manager.css',
  'ui/common/styles/metrics.css',
  'ui/common/styles/modal.css',
  'ui/common/styles/panels.css',
  'ui/common/styles/scrollbars.css',
  'ui/common/styles/segmentedToggle.css',
  'ui/common/styles/tables.css',
  'ui/common/styles/toast.css',
  'ui/imageProcessingDisplay/imageProcessingDisplay.css',
  'ui/imageViewerDisplay/imageViewerDisplay.css',
  'ui/observationExecution/executionQueue.css',
  'ui/observationExecution/observationExecution.css',
  'ui/observationManager/executionQueue.css',
  'ui/observationManager/observationManager.css',
  'ui/observationPlanner/observationPlanner.css',
  'ui/observatoryManager/panels/IndiDevicePanel.css',
  'ui/observatoryManager/panels/IndiPropertyItem.css',
  'ui/planetariumDisplay/styles/planetariumDisplay.css',
  'ui/statusBar/statusBar.css',
  'ui/statusHeader/statusHeader.css',
  'ui/statusHeader/terminal.css',
  'ui/titleBar/titleBar.css',
  'ui/trayPopover/trayPopover.css',
];

/** Values that are never a raw design value, so the strict-value rule lets them through. */
const NEUTRAL_VALUES = ['inherit', 'initial', 'unset', 'revert', '0', 'none', 'transparent', 'currentColor', '50%', '100%'];

export default {
  files: ['ui/**/*.css'],
  ignoreFiles: ['**/node_modules/**', 'ui/coverage/**', 'ui/common/styles/theme.css', ...LEGACY_STYLESHEETS],
  plugins: ['stylelint-declaration-strict-value'],
  rules: {
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
