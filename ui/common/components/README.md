# Shared components

This folder holds the React components that more than one display uses. A component here must not import from any other top-level `ui/` folder. ESLint enforces that rule. The user manual explains how each display uses them.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This file also explains each term when it first appears.

## Building blocks

Six small components give the displays one look and one set of keyboard and screen-reader rules. Each one has tests in `tests/`.

- `Card.tsx` is a bordered box with an optional title bar. A `selected` card has a blue outline. A `current` card sets `aria-current` and a thick left edge. A card with an `onClick` can be focused and works with Enter and Space.
- `Tabs.tsx` is a tab strip that follows the WAI-ARIA tab pattern (arrow keys, Home, End and `aria-selected`). WAI-ARIA is the web standard that makes controls readable by screen readers. A tab can show a count. `TabPanel` in the same file is the matching panel.
- `InfoButton.tsx` is a small "i" button. Its text shows on hover, on keyboard focus and on click, so keyboard and touch users can read it. Escape or moving focus away hides it.
- `StatusChip.tsx` is a small pill with an icon and a word, so a status is never told by color alone. Its statuses are ok, warning, error, info and neutral, and each has its own icon shape.
- `ProgressBar.tsx` is a bar with `role="progressbar"` and visible text such as "12 of 20 frames". The text is also the bar's `aria-valuetext`.
- `FormRow.tsx` is a label, an input slot, an optional unit and an inline error. The label points to the input with `htmlFor`. An error shows as an alert under the input.

## Other components

- `SectionPanel.tsx`, `GenericDisplayLayout.tsx` and `TabView.tsx` lay out the displays.
- `BaseModal.tsx`, `ConfirmDialog.tsx`, `AddTargetModal.tsx`, `FitsHeaderModal.tsx` and `IngestFramesModal.tsx` are dialogs.
- `CustomSelect.tsx`, `SelectableList.tsx`, `ListActions.tsx` and `Popover.tsx` are controls.
- `EmptyState.tsx`, `LoadingSpinner.tsx` and `ErrorBoundary.tsx` show the empty, loading and failed states.
- `quality/` holds the statement components: `StatementList.tsx`, `StatementItem.tsx`, `ConfidenceChip.tsx`, `QualityChip.tsx` (the star Quality card) and the words they use (`statementLabels.ts`). A statement shows its plain message, a kind word and a confidence word. The Result kind marks a finding such as a star class.
- `plotting/` holds the shared chart components.

## Styling rule

Colors, font sizes, spacing and corner radii come from the tokens in `ui/common/styles/theme.css` (for example `var(--accent-blue)`, `var(--small-font-size)`, `var(--space-2)` and `var(--radius-md)`). Stylelint enforces this for every stylesheet under `ui/` (`npm run lint:css`, part of `npm run lint`). The settings are in `stylelint.config.mjs`. Older stylesheets that still hold raw values are listed there in `LEGACY_STYLESHEETS`. That list only shrinks. Clean a file, then delete its line. Never add a file to it. A new or changed stylesheet must pass without an entry.

For exact behavior, read the code.
