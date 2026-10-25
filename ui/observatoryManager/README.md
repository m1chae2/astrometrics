# Observatory Manager

This folder holds the Observatory Manager display. It is where you set up and watch the equipment: the mount, the camera profiles, the safety limits, guiding, the calibration library and the INDI devices. INDI is the open standard that lets software talk to astronomy hardware. The user manual describes every control: see [Observatory Manager](../../documentation/user_interface/user_guides/User_Manual.md#observatory-manager).

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Who controls the equipment

Ekos (the KStars imaging program) captures the frames for now, and this app only watches. The rule that blocks commands lives in `wayfindinglib`, which refuses a command to any capability that it handed to Ekos. The display adds a clear message on top of that rule:

- `ui/common/hooks/useCommandAvailability.ts` (shared with the Observation Manager and the Planetarium) reads the delegation state (`observatory:safety_status`). Until the library says the app may command a capability, the answer is no.
- `CommandGate.tsx` greys the controls that send commands and shows the reason when you hover over them. Its `WhyUnavailableButton` adds a Why unavailable? info button to the panel title, so keyboard and touch users can read the reason too.

Controls that do not command a device, such as Add Target, stay on.

## What each file is for

- `ObservatoryManager.tsx` puts the display together. `ControlPanel.tsx` lays out the center column in rows.
- `panels/` holds the control and information panels: `TelescopeControl.tsx`, `TrackingSettings.tsx` (two tabs), `DirectionalControl.tsx` (Manual Guide; the pad scales with the panel, and on a narrow panel the slew-speed slider wraps below it), `LimitSettings.tsx` (the whole Safety Limits panel), `AlignmentGuidingTabs.tsx` (Alignment and Guiding Target tabs, using `AlignmentStatus.tsx`, whose table shows each attempt's time, target, status word and deltas as the library records them), `GuidingStatusPanel.tsx` (the whole Guiding Trends panel, with a short message instead of an empty plot before any samples, plus the target plot, which fills its tab and redraws when the tab is shown or resized) and the `Indi*` files for devices and their properties.
- `equipment/` holds `CameraProfilesPanel.tsx` (profiles, recorded noise and Measure noise).
- `calibration/` holds `CalibrationLibraryPanel.tsx`. It sits in the top row, to the right of Tracking Settings. Line 1 is the camera picker and a small refresh button. Line 2 is the Darks, Biases and Flats counts for that camera. The Darks table is always shown below, keeps room for four rows, grows into any extra row height and scrolls inside the panel. `formatExposure` writes sub-second exposures in milliseconds. Ingest frames is a header button, like the other panels' header buttons. A match on a dark row scrolls the table to the row. The camera list and the totals come from the library's `camera_totals`, in the order returned. A statement can ask it to show a row ("Show in calibration library"). The panel asks the library which rows match (`calibration:query` with detail `match`) and highlights them (a bias or flat match highlights its count). `calibrationRowRequestStore.ts` carries the request.
- `IndiStatusPanel.tsx` is the right column. `hooks/useObservatoryControl.ts` holds the control handlers.
- `tests/` holds the Vitest tests.

## Safety Limits

The Safety Limits panel shows the active telescope's saved limits on two tabs: Altitude (the altitude window) and Hour angle (the hour angle limit and the meridian flip point). Apply safety limits sits at the bottom of the panel, in view without scrolling. Apply safety limits saves a changed form through `observatory:set_active_telescope` with a `limits` value. The library checks the numbers, such as a minimum altitude above the maximum, and refuses to create a configuration section for a telescope that has none. The panel shows the library's reason. After a save, a message reminds you to copy the session into Ekos again, because Ekos keeps its own copy.

## Camera Profiles

The panel shows a compact table with one row per camera setting: the camera name, with the setting (gain, offset, binning and temperature) on a second line. It gives the gain (electrons per analog-to-digital unit, one step of the stored pixel value) and the read noise (electrons) for each camera setting. A small chip under each number tells how far to trust it: Measured, Partly measured or Tentative (a datasheet or assumed value). Cameras with no recorded noise are listed on one line that starts with Not measured. Measure noise asks the library to measure on flat and bias frames (`calibration:measure_sensor_noise`). Measure noise in the panel header opens the measurement form; focus moves into it, and Close or Escape shuts it and returns focus to the button. It saves nothing until you confirm.

## Layout note

The page does not scroll. A panel that is too small for its content scrolls inside itself. At a window of 1024 by 700 pixels, the middle row shrinks to its title, and you scroll inside them.

The row budget is in a comment in `observatoryManager.css`. The top row holds Telescope Control, Tracking Settings (both as wide as their content) and the Calibration Library (the rest). Its height is set by Telescope Control's three button rows or the Calibration Library's four Darks rows, whichever is taller. The middle row holds three panels (up to 290 pixels). The guiding row takes the rest, shared three to two by Guiding Trends (the trace check boxes and History slider are in its header) and the tabbed Alignment and Guiding panel, which is wide enough for target names; on a narrow panel the Alignment table drops its Time column first.

For exact behavior, read the code.
