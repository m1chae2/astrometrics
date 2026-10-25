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
- `panels/` holds the control and information panels: `TelescopeControl.tsx`, `TrackingSettings.tsx`, `DirectionalControl.tsx` (Manual Guide), `LimitSettings.tsx` (Safety Limits), `AlignmentStatus.tsx`, `GuidingStatusPanel.tsx` and the `Indi*` files for devices and their properties.
- `equipment/` holds `CameraProfilesPanel.tsx` (profiles, recorded noise and Measure noise).
- `calibration/` holds `CalibrationLibraryPanel.tsx`. It shows the Darks table and the Biases and Flat Frames totals for one camera. A statement can ask it to show a row ("Show in calibration library"), and `calibrationRowMatch.ts` and `calibrationRowRequestStore.ts` carry that request.
- `IndiStatusPanel.tsx` is the right column. `hooks/useObservatoryControl.ts` holds the control handlers.
- `tests/` holds the Vitest tests.

## Safety Limits

The Safety Limits panel shows the active telescope's saved limits: the altitude window, the hour angle limit and the meridian flip point. Apply safety limits saves a changed form through `observatory:set_active_telescope` with a `limits` value. The library checks the numbers, such as a minimum altitude above the maximum, and refuses to create a configuration section for a telescope that has none. The panel shows the library's reason. After a save, a message reminds you to copy the session into Ekos again, because Ekos keeps its own copy.

## Camera Profiles

Each profile lists the camera, its pixel size and its sensor size. The Recorded noise table gives the gain (electrons per analog-to-digital unit, one step of the stored pixel value) and the read noise (electrons) for each camera setting. An Evidence word tells how far to trust each number: Measured, Partly measured or Tentative (a datasheet or assumed value). A camera with no recorded noise reads Not measured. Measure noise asks the library to measure on flat and bias frames (`calibration:measure_sensor_noise`). It saves nothing until you confirm.

## Layout note

The page does not scroll. A panel that is too small for its content scrolls inside itself. At a window of 1024 by 700 pixels, the middle and calibration rows shrink to their titles, and you scroll inside them.

For exact behavior, read the code.
