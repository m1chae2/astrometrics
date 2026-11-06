# Shared components

This folder holds the React components that more than one display uses. A component here must not import from any other top-level `ui/` folder. ESLint enforces that rule. The user manual explains how each display uses them.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This file also explains each term when it first appears.

## Building blocks

Small components give the displays one look and one set of keyboard and screen-reader rules. Each one has tests in `tests/`.

- `Card.tsx` is a bordered box with an optional title bar. A `selected` card has a blue outline. A `current` card sets `aria-current` and a thick left edge. A card with an `onClick` can be focused and works with Enter and Space.
- `Tabs.tsx` is a tab strip that follows the WAI-ARIA tab pattern (arrow keys, Home, End and `aria-selected`). WAI-ARIA is the web standard that makes controls readable by screen readers. A tab can show a count. `TabPanel` in the same file is the matching panel. Tabs have one look: muted text with a 3px blue underline under the selected tab. Pass `size="compact"` for a tight panel (narrower side padding). `TabView.tsx` draws its header with `Tabs`. Two-way switches such as "By catalog / By camera" also use `Tabs` (the sort order of the target list too), so there is no second tab look.
- `InfoButton.tsx` is a small "i" button. Its text shows on hover, on keyboard focus and on click, so keyboard and touch users can read it. Escape or moving focus away hides it.
- `StatusChip.tsx` is a `Badge` with an icon and a word, so a status is never told by color alone. Its statuses are ok, warning, error, info and neutral, and each has its own icon shape. `quality/ConfidenceChip.tsx` is also a `Badge`.
- `ProgressBar.tsx` is a bar with `role="progressbar"` and visible text such as "12 of 20 frames". The text is also the bar's `aria-valuetext`.
- `FormRow.tsx` is a label, an input slot, an optional unit and an inline error. The label points to the input with `htmlFor`. An error shows as an alert under the input.

## Controls

Every button, text field, checkbox and badge in the app comes from one of these components. Do not write a raw `<button>`, `<input>` or `<select>` in any folder outside `ui/common/components/`. Each component owns one stylesheet next to it. A layout `className` (margin, position, flex) is fine; colors and sizes are not.

| Need | Use |
| --- | --- |
| An action | `Button.tsx`. `variant` is `primary`, `secondary`, `danger` or `ghost` (no fill). Leave it out for the plain blue button. `size` is `small` (24px tall) or `square` (a 36px icon button). `pressed` makes a toggle (it sets `aria-pressed`). `active` only draws the active look. `control` is for hardware command buttons. A button is `type="button"` unless you pass `type="submit"`. |
| An icon, a close mark or a row that is a button | `Button` with `variant="ghost"`. Its rules have no weight, so the row's own class decides the look. |
| A small status or tag | `Badge.tsx` with a `tone`: ok, warning, error, info, neutral, accent or purple. With `onClick` it is a button. `StatusChip` adds an icon and a word. |
| One choice from a list | `PopoverSelect.tsx` (no native select anywhere). |
| Text, password or search | `TextInput.tsx` (`width="short"` or `"fill"`, `centered`). |
| A number | `NumberInput.tsx`. It takes the same props, plus `min`, `max` and `step`. |
| A yes or no | `Checkbox.tsx`. Pass `label`, or `aria-label` for a box with no text. |
| A range | `Slider.tsx`. |
| A file picker | `FileInput.tsx`. Hide it and open it from a `Button`. |
| Two or more views | `Tabs.tsx`. |

The `btn` class family lives in `Button.css`: 36px tall, 14px text and 6px corners; the small size is 24px tall with 11px text. Text fields use `.entry` (`TextInput.css`): 38px tall with 6px corners. Badge rules live in `Badge.css`. Checkboxes, radio buttons and sliders take the theme accent color.

### Allowed raw elements

These are the only raw controls. All of them are inside `ui/common/components/`, which the lint rule skips.

- The component files themselves: `Button.tsx`, `TextInput.tsx`, `Checkbox.tsx`, `Slider.tsx` and `FileInput.tsx` render the native element.
- `Tabs.tsx` (a tab is a native button with `role="tab"` and its own arrow-key handling), `PopoverSelect.tsx` (its trigger and menu entries) and `InfoButton.tsx` (its trigger, which owns focus and the hover text): their look is a tab, a menu row or an info mark, not a regular button.
- `SelectableList.tsx` draws one radio input per row. A list can hold thousands of rows, so each row is kept as light as possible.

## Other components

- `SectionPanel.tsx`, `GenericDisplayLayout.tsx` and `TabView.tsx` lay out the displays.
- `BaseModal.tsx`, `ConfirmDialog.tsx`, `AddTargetModal.tsx`, `FitsHeaderModal.tsx` and `IngestFramesModal.tsx` are dialogs.
- `SelectableList.tsx`, `ListActions.tsx` and `Popover.tsx` are controls.
- `PopoverSelect.tsx` is the shared single-choice picker. It takes a label, a list of `{id, label, detail?}` and the picked id. Pass `emptyLabel` to add a first "nothing picked" choice that reports `null`. It is our own menu, so it follows the dark theme, unlike a native select. Use it instead of a native select: no screen in the app uses one now. `disabled` dims it, `size="compact"` makes a small toolbar-size button, and `placement="above"` opens the menu upward for a picker at the bottom of a panel. `SessionPicker.tsx` is `PopoverSelect` with a "Live" first choice. The Planetarium toolbar and the Observatory Manager use it.

- `EmptyState.tsx`, `LoadingSpinner.tsx` and `ErrorBoundary.tsx` show the empty, loading and failed states.
- `quality/` holds the statement components: `StatementList.tsx`, `StatementItem.tsx`, `ConfidenceChip.tsx`, `QualityChip.tsx` (the star Quality card) and the words they use (`statementLabels.ts`). A statement shows its plain message, a kind word and a confidence word. The Result kind marks a finding such as a star class.
- `plotting/` holds the shared chart components.

## Styling rule

Colors, font sizes, spacing and corner radii come from the tokens in `ui/common/styles/theme.css` (for example `var(--accent-blue)`, `var(--small-font-size)`, `var(--space-2)` and `var(--radius-md)`). Stylelint enforces this for every stylesheet under `ui/` (`npm run lint:css`, part of `npm run lint`). The settings are in `stylelint.config.mjs`. Older stylesheets that still hold raw values are listed there in `LEGACY_STYLESHEETS`. That list only shrinks. Clean a file, then delete its line. Never add a file to it. A new or changed stylesheet must pass without an entry.

For exact behavior, read the code.
