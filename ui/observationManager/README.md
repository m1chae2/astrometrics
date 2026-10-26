# Observation Manager

This folder holds the Observation Manager display. It is where you keep observation packages, plan tonight's session and follow how far the night got. The user manual describes every control: see [Observation Manager](../../documentation/user_interface/user_guides/User_Manual.md#observation-manager). The [desktop app architecture](../../documentation/user_interface/technical_reference/User_Interface_Architecture.md) explains how the display gets its data.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Key words

- An *observation package* is the plan for one target: exposure rows, a dither setting and a framing. It is Suggested, Planned or Complete.
- An *observation session* is one night's ordered list of packages. There is one session for each night. The *Execution Queue* shows tonight's session.
- A *run state* says how a queue card stands: Imaging now, Not imaging, Done, Partly done, Not run or Progress unknown.

## What the display does

The page does not scroll. The Execution Queue runs across the top. Under it are the package library on the left, the package editor in the center and the Visible Targets table on the right. You can work on one thing at a time. Picking a package or a queue card clears the other pick.

The display only shows what the library returns. The library chooses which packages exist, counts the frames, and writes the plain sentences. The display does no ranking, counting or wording.

## What each file is for

- `ObservationManager.tsx` puts the display together. It owns the one selection and handles New package, Delete package, Dismiss, Undo and Refresh suggestions.
- `ExecutionQueue.tsx` and `ImagingCard.tsx` draw the queue. A card is kept minimal. Its first line shows the target name, the total run time (such as "3 min") and the Move earlier, Move later, Edit and Remove buttons; its second line shows a run-state chip and a progress bar such as "12 of 20 frames". It does not repeat the exposure rows; the Image List in the editor shows them. The strip is as tall as one card, so it never scrolls up and down. It scrolls sideways only when there are more cards than fit the window. `ChecklistDialog.tsx` is the Checklist (N) list with its tick boxes. Each item shows the plain-words label the library gives it.
- `PackageLibraryPanel.tsx` draws the three tabs, the buttons and the cards. `NewPackageSearch.tsx` is the search box behind New package.
- `PackageEditor.tsx` holds, for the open package, the Image Planner with Exposures (`ImagePlanner.tsx`) and Dither (`DitherSettings.tsx`) tabs, the Image List (`ImageList.tsx`) under it with Add to Sequence and Delete package in its title bar, and Last Image (`LastImageViewer.tsx`) in the right column. The planner's filter list is the observatory's configured filters (`hooks/useAvailableFilters.ts`, reading `system:filters`); `packageModel.ts` `filterLabel` shows the library's names by their short form ("Luminance" as "L", "Star Analyzer 200" as "SPEC").
- `TargetListStatus.tsx` is the Visible Targets table. A click on a row opens the target's package. Until (UTC) is the library's UTC setting time, shown to the minute. `PackageLibraryPanel.tsx` keeps New package and Add Target in its title bar and Refresh suggestions at the top of the Suggested tab.
- `packageModel.ts` holds small helpers that turn library values into display words, such as the run-state words.
- `hooks/` holds the data hooks: `usePackageLibrary.ts` (the three lists and the package actions), `useCurrentSession.ts` (tonight's session and the checklist tick), `usePackageDraft.ts` (the open package's form), `useExecutionQueue.ts` and `useTargetVisibility.ts`.
- `context/PlanningContext.tsx` holds the plan row type that the editor uses.
- `tests/` holds the Vitest tests.
- The `.css` files style the panels. They use the tokens in `ui/common/styles/theme.css`.

## Where the data comes from

- `plan:current_session` returns tonight's session with the frames captured for each entry. The display reads it again each minute, because frames arrive with no message.
- `plan:list_packages`, `plan:create_package` and `plan:edit_package` read and change packages. Each edit names the revision that the window last read. The library refuses an edit made from an old revision, and the display shows the refusal as text.
- `plan:refresh_suggestions` asks the library to look again at which targets need more frames.
- The `packages:changed` and `plan:changed` events mark these reads as stale. `ui/App.tsx` listens for them.
- The `sequencer:` methods hold the queue that Start Imaging Session runs. Start Imaging Session and Test Image are device commands. They are greyed, with a Why unavailable? button, while Ekos controls the equipment (`ui/common/hooks/useCommandAvailability.ts`).

## Measures this display shows

- *Frames captured of frames requested*: counts for a queue entry, from the library's frame counting. A frame counts when it matches the row's filter, exposure, gain, offset and binning and has not been set aside. A frame the library has not judged yet counts as captured but does not complete the row.
- *Imaging now*: set when a frame for an entry arrived in the last few minutes (Design estimate; see [WAY-D84](../../documentation/library_design/validation/wayfinding.md#design-estimates)).
- *Confidence chip*: Measured, Partly measured or Tentative, shown on a suggested package. It comes from the evidence behind the suggestion.

## Rules

- Sessions are set up by hand here and in Ekos for now. The sequencer stays in the code so that the app can run sessions without Ekos later.
- Target-level quality statements are not shown here. They are for the library and the AI companion.
- The old names Observation Planner and Observation Execution open this display (`ui/common/utils/normalizeAppMode.ts`).

For exact behavior, read the code.
