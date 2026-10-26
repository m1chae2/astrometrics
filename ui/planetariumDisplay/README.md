# Planetarium

This folder holds the Planetarium display. It draws the sky from your site, lets you find objects and send the telescope to them, and sends targets and framing to the observation packages. The user manual describes every control: see [Planetarium](../../documentation/user_interface/user_guides/User_Manual.md#planetarium).

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## What each folder is for

- `PlanetariumDisplay.tsx` puts the display together: the target and star lists, the sky map, the toolbar, the time bar and the Framing panel.
- `components/` holds the map and its panels. `CelestialSkyMap.tsx` draws the map, `PlanetariumInfoCard.tsx` is the card for a clicked object, `FramingPanel.tsx` is the framing panel, and `PlanetariumToolbar.tsx`, `PlanetariumContextMenu.tsx`, `PlanetariumDateTimeModal.tsx` and `AlignmentPointCard.tsx` hold the other controls.
- `hooks/` holds the data hooks. `useTargetPackage.ts` and `useMosaicPreview.ts` serve the package actions (see below).
- `layers/` holds the overlays drawn on the map, such as stars, grid, constellations, the field of view and the framing outline (`FramingOverlay.ts`).
- `showOnSky/` holds the small store that lets another display ask the map to center on a target.
- `webgl/`, `utils/` and `styles/` hold the star renderer, the projection and time helpers and the stylesheets.
- `tests/` holds the Vitest tests.

## Package actions

The Planetarium sends two things to the observation packages. Both work on the package of the chosen target (`hooks/useTargetPackage.ts`).

- *Add to observation package* is a button on the information card. It looks for the target's planned package, then its suggested package. If it finds neither, the library creates an empty package (`plan:create_package`). For a catalog star that is not in the library yet, the library first creates the target from its catalog name. If the library cannot find the name, it refuses, and the card shows the reason under the button.
- *Send framing to package* is a button in the Framing panel. It stores the center, the rotation and the panels in the target's package.

## Framing panel

The Framing button opens a panel for the chosen target. You set Rows, Columns, Overlap (%) and a rotation. The panel asks the library for the panel centers and corners (`mosaic:preview`) and draws each panel's outline on the map. The rotation is in degrees east of north, as Ekos and KStars give it. The words that explain the rotation also come from the library. The Planetarium does no framing math. Send framing to package stays off, with its reason shown, until a target is chosen and the library has returned panels.

## Device commands

Slew Telescope Here, in the right-click menu, is a device command. It is greyed, with a Why unavailable? button, while Ekos controls the equipment (`ui/common/hooks/useCommandAvailability.ts`).

## Shared time and target

The current target and the planning time follow the backend's handoff state, so a second Planetarium window shows the same target and time (`ui/common/context/HandoffContext.tsx`). Choosing a time in the Date & Time dialog, or clicking an alignment point, sets the shared planning time.

For exact behavior, read the code.
