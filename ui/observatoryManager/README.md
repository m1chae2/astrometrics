# Observatory Manager

This folder holds the Observatory Manager display. It is where you set up and watch the equipment: the mount, the camera profiles, the safety limits, guiding, the calibration library and the INDI devices. INDI is the open standard that lets software talk to astronomy hardware. The user manual describes every control: see [Observatory Manager](../../documentation/user_interface/user_guides/User_Manual.md#observatory-manager).

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Who controls the equipment

Ekos (the KStars imaging program) captures the frames for now, and this app only watches. The rule that blocks commands lives in `wayfindinglib`, which refuses a command to any capability that it handed to Ekos. The display adds a clear message on top of that rule:

- `ui/common/hooks/useCommandAvailability.ts` (shared with the Observation Manager and the Planetarium) reads the delegation state (`observatory:safety_status`). Until the library says the app may command a capability, the answer is no.
- `CommandGate.tsx` greys the controls that send commands and shows the reason when you hover over them. Its `WhyUnavailableButton` adds a Why unavailable? info button to the panel title, so keyboard and touch users can read the reason too.

Controls that do not command a device, such as Add Target, stay on while Ekos is in control. They are greyed while a past night is shown (see below).

## Night mode

The Session panel, at the top of the left column, holds the shared session picker (`ui/common/components/SessionPicker.tsx`, the same component the Planetarium toolbar uses). It starts on Live. Its other entries are the observing nights from `history:nights`: the date, with the targets imaged as the detail. The choice lives in `hooks/useObservatoryNightPicker.ts`, which reads the list again when the backend sends `library:guiding_updated`. The panel also holds the banner (`data-testid="night-banner"`, `role="status"`) that says a past night is shown. `ObservatoryNightContext.tsx` hands the choice to every panel. Without a provider the context says Live, so a panel used alone works as before.

With a night picked, a `role="status"` line beside the picker reads "Viewing the night of <date>. Controls are live-only; pick Live to use them." The panels change like this:

- The controls that command equipment or change the library are disabled, not hidden, with that sentence as their tooltip: Telescope Control (every tab), Manual Guide, Slew to Target, Add Target, Download Frames, Ingest frames, Measure noise and the Device Properties edits.
- Guiding Trends shows that night's summary (`GuidingNightSummaryView`, from `history:night_quality`).
- Alignment and Guiding lists that night's attempts. `hooks/useNightAlignmentAttempts.ts` finds the night's session in `telescope:list_alignment_sessions` (the Planetarium's list) and reads it with `telescope:get_session_alignment`. A night with no session shows an empty message. The Guiding target tab plots live samples only, so for a night it says so.
- The Targets panel shows only the targets in the night's `targetsImaged`, each with the frame count and warning count from `history:night_quality`.  Live shows the full list.

## What each file is for

- `ObservatoryManager.tsx` puts the display together. Its left column has three panels, top to bottom: Session (the night picker and banner), Targets and Controls (Add Target, Download Frames, Slew to Target). Targets is one panel with two tabs, in the same compact style as the Telescope Control tabs. The List tab is the one shown at the start: a small muted line that names the filter in use (for example "Messier Catalog · Newest"), the search box (`RadioListFiltering`) and the target list (`SelectableList`), which takes the rest of the panel height and scrolls. The Filter tab holds the By catalog and By camera toggle with its option list and the A-Z and Newest sort (`TargetListFilterPanel`). A filter choice is kept, so the user switches back to List to see the result. The same pieces are shared with the other views; only this view puts them in tabs. The Session panel is as tall as its content, with no scrollbar, and its picker menu opens over the panels below it. `ControlPanel.tsx` lays out the center column in rows.
- `panels/` holds the control and information panels: `TelescopeControlPanel.tsx` (the whole Telescope Control panel, with five tabs), `TelescopeControl.tsx` (the alignment, tracking and park buttons of the Controls tab), `TrackingSettings.tsx` (the Alignment tab or the Guiding tab, chosen by its `section` prop; the Guiding tab has Start Guiding and Stop Guiding, and a failed start or stop shows a toast), `DirectionalControl.tsx` (Manual Guide; the pad scales with the panel, and in the square Manual Guide panel the slew-speed slider wraps below it), `LimitSettings.tsx` (the Altitude tab and the Hour angle tab, which share one form), `AlignmentGuidingTabs.tsx` (Alignment and Guiding Target tabs, using `AlignmentStatus.tsx`, whose table shows each attempt's time, target, status word and deltas as the library records them), `GuidingStatusPanel.tsx` (the whole Guiding Trends panel. With no live samples it shows the summary of the newest night that `history:nights` says has guiding data, titled "Last night with guiding: <date>", and switches back to the plot when samples arrive; the short message stays only when no night has guiding data, plus the target plot, which fills its tab and redraws when the tab is shown or resized) and the `Indi*` files for devices and their properties.
- `equipment/` holds `CameraProfilesPanel.tsx` (profiles, recorded noise and Measure noise).
- `calibration/` holds `CalibrationLibraryPanel.tsx`. It sits at the right of the top block and spans both of its rows, so its table is tall. Line 1 is the camera picker (a `PopoverSelect`) and a small refresh button. Line 2 is three compact `Tabs` that pick the table below. Each tab carries the count for that camera, such as "Darks (25)". The table of the chosen kind keeps room for four rows, grows into any extra row height and scrolls inside the panel. Darks show Exposure, Offset, Temp, Count and Health. Biases show Gain, Offset, Count and Health. Flats show Filter, Gain, Offset, Count and Health. The Health column comes from `calibration:assess`, a separate query that is joined to the rows by `row_key` and is not part of the 10-second poll of `calibration:query`. A set reads OK, Warning or Not checked, with a shape as well as the word. Not checked is a different answer from OK. Choosing a health chip opens the plain reason, each check and the files to look at (`calibrationHealth.tsx`). There is no button to measure the sets: the backend starts that job (`calibration:measure_frames`) by itself after an ingest or a calibration folder sync adds dark or bias frames, and sends the `calibration:measured` event when it ends, which makes the panel read the health again. `formatExposure` writes sub-second exposures in milliseconds. Ingest frames is a header button, like the other panels' header buttons. A match on a dark row scrolls the table to the row. The camera list and the totals come from the library's `camera_totals`, in the order returned. A statement can ask it to show a row ("Show in calibration library"). The panel asks the library which rows match (`calibration:query` with detail `match`) switches to the kind's tab and highlights them. `calibrationRowRequestStore.ts` carries the request.
- `IndiStatusPanel.tsx` is the right column. `hooks/useObservatoryControl.ts` holds the control handlers.
- `tests/` holds the Vitest tests.

## Telescope Control

Telescope Control is the one panel for starting alignment, tracking, guiding or parking, and for the limits that can affect them. It has five tabs, in this order:

- **Controls** holds Start Alignment and Stop Alignment, Tracking ON and OFF, and PARK and UNPARK.
- **Alignment** holds Accuracy and Settle.
- **Guiding** holds Exposure (s), Gain, Start Guiding and Stop Guiding.
- **Altitude** holds Enable altitude limits, Minimum altitude and Maximum altitude.
- **Hour angle** holds Enable hour angle limit, Maximum hour angle and Meridian flip after transit. The flip point is stored in degrees (`flip_hour_angle_deg`), so its unit is degrees past transit.

Both limit tabs show the active telescope's name and the same Apply safety limits button. They share one form, so an edit on one tab is kept while you look at the other, and one Apply saves both through `observatory:set_active_telescope` with a `limits` value. The library checks the numbers, such as a minimum altitude above the maximum, and refuses to create a configuration section for a telescope that has none. The tab shows the library's reason. After a save, a message reminds you to copy the session into Ekos again, because Ekos keeps its own copy.

One Why unavailable? button in the panel header explains why the controls are greyed. Every tab greys its live controls with the same reason, whether commands are off or a past night is shown. The tab row wraps instead of scrolling when the panel is narrow. This panel only shows and saves the limits. How the library applies them is tracked in issue #156.

## Camera Profiles

The panel shows a compact table with one row per camera setting: the camera name, with the setting (gain, offset, binning and temperature) on a second line. It gives the gain (electrons per analog-to-digital unit, one step of the stored pixel value) and the read noise (electrons) for each camera setting. A small chip under each number tells how far to trust it: Measured, Partly measured or Tentative (a datasheet or assumed value). Cameras with no recorded noise are listed on one line that starts with Not measured. Measure noise asks the library to measure on flat and bias frames (`calibration:measure_sensor_noise`). Measure noise in the panel header opens the measurement form; focus moves into it, and Close or Escape shuts it and returns focus to the button. It saves nothing until you confirm.

## Layout note

The page does not scroll. A panel that is too small for its content scrolls inside itself. At a window of 1024 by 700 pixels, the middle row shrinks to its title, and you scroll inside them.

The row budget is in a comment in `observatoryManager.css`. The top block is a grid with two rows. Row 1 holds Telescope Control and Manual Guide, which is a square (its height follows the window height, 190 to 260 pixels). Row 2 holds Camera Profiles under both (90 to 160 pixels). The Calibration Library takes the rest of the width and spans both rows. In the square, the pad sits on top and the Slew Speed slider wraps below it. Telescope Control scrolls inside itself if a tab is taller. The guiding row takes the rest, shared three to two by Guiding Trends (the trace check boxes and History slider are in its header) and the tabbed Alignment and Guiding panel, which is wide enough for target names; on a narrow panel the Alignment table drops its Time column first.

Every button, text box, number box, check box and slider here comes from the shared parts in `ui/common/components/` (`Button`, `TextInput`, `NumberInput`, `Checkbox`, `Slider`). This folder's CSS only places them; it does not restyle them.

For exact behavior, read the code.
