# Planetarium

This folder holds the Planetarium display. It draws the sky from your site, lets you find objects and send the telescope to them, and sends targets and framing to the observation packages. The user manual describes every control: see [Planetarium](../../documentation/user_interface/user_guides/User_Manual.md#planetarium).

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## What each folder is for

- `PlanetariumDisplay.tsx` puts the display together: the target and star lists, the sky map, the toolbar, the time bar and the Framing panel.
- `components/` holds the map and its panels. `CelestialSkyMap.tsx` draws the map, `PlanetariumInfoCard.tsx` is the card for a clicked object, `FramingPanel.tsx` is the framing panel, and `PlanetariumToolbar.tsx`, `PlanetariumContextMenu.tsx`, `PlanetariumDateTimeModal.tsx` and `AlignmentPointCard.tsx` hold the other controls.
- `hooks/` holds the data hooks. `useTargetPackage.ts` and `useMosaicPreview.ts` serve the package actions (see below).
- `layers/` holds the overlays drawn on the map, such as stars, grid, constellations, the field of view and the framing outline (`FramingOverlay.ts`).
- Another display asks the map to center on a target with the `showOnSky` hand-off (see `ui/common/utils/README.md`). `PlanetariumDisplay.tsx` waits until the target lists have loaded, then acts on it. The `locateStar` hand-off from the Astronomy Manager works the same way. Both arrive even if the Planetarium was hidden when they were sent.
- While the Planetarium is hidden, `App.tsx` pauses it: the drawing loop, the time playback and the arrow-key listeners stop, and start again when it is shown. The map's view and the time offset are kept.
- `webgl/`, `utils/` and `styles/` hold the star renderer, the projection and time helpers and the stylesheets.
- `tests/` holds the Vitest tests.

## Left column

The left column works in two levels, in both *By target* and *By spectral class*. Level 1 is the target list (or the class list) at the full height of the column. Picking an entry centers the map on it, as before, and slides the column to level 2: the stars of that target (or class), with the entry's name and a *‹ Targets* (or *‹ Classes*) button in the title row. The button, or Escape, slides back to level 1 with the entry still picked. The slide uses the Framing panel's timing and is off when the system asks for reduced motion. To keep it smooth, the map centers on the target only after the slide ends, and a target's first page of stars starts loading while the pointer rests on its row (`prefetchAstronomyList`), so the star list is filled as it slides in. The level that is off screen is inert, so Tab skips it. Arrow keys move through level 1 and move the map, but do not slide; Enter opens the picked entry. Each mode keeps its own level. A *Show on sky* request, and a *Locate in Planetarium* request that names the star's target (the Astronomy Manager sends the target picked in its list), open that target's star list.

Under the target search, two compact pickers set the shared target filter (`targetList.filterPanel` from `useTargetListLogic`, the same choices as Image Processing's *Filter Targets* panel). *Show* lists the catalogs (*All targets*, *Messier*, *NGC IC*, *Stars*, *Planets*, *No image*) and then each camera; picking one clears the other kind. *Sort* is *Newest* or *A–Z*. The choices are shared with the other screens.

The sky follows the same filters. The target markers and the *Images* layer show only the library targets that pass the *Show* pick and, when it is on, *Above horizon only* (`utils/skyTargetFilter.ts`). One set of visible targets serves both, and a hidden target's picture is never loaded. The search text and the sort order do not change the sky, and the selected target stays drawn. Markers that are not library targets are not filtered.

## Targets below the horizon

In *By target*, a target that is at or under the horizon (altitude 0° or less) at the planning time gets a small grey *Below* badge beside its S and P badges. Targets that are up get no badge. The *Above horizon only* box at the right end of the Targets title row hides the badged targets; the choice is remembered in this browser. The check uses the library targets' positions and your site (`hooks/useTargetsBelowHorizon.ts`). It runs once for a fixed planning time and once a minute when the time is Now. A target with no position gets no badge, and the filter hides it. Until the site has loaded there are no badges and the box is greyed.

## Spectral class view

With the left column on *By spectral class*, the map's own stars are every star of the class lists and every star with a spectrum, whatever target is chosen, drawn in its class color (`planetarium:get_class_stars`, `hooks/useClassStarSources.ts`). The color comes from our own spectrum's type, then the catalog type, then the catalog B-V color (`utils/starColor.ts`); the colors are the `--star-color-*` tokens in `theme.css`, and a legend shows them. Picking a class keeps only that catalog class. *By target* is unchanged. The left column folds to a strip like the Astronomy Manager's; the strip names the target (or class) and the picked star.

## Toolbar and sessions

The toolbar is the shared compact `Toolbar` (small labels, compact controls, one text size). With *No Session Selected* the map draws no session overlays; picking a session draws its pointings, syncs and error arrows. While tracking is on, the time bar shows a *Tracking equatorial* chip.

## Images layer

The toolbar's *Images* switch (remembered) lays each target's processed stack picture on the sky; its opacity is in the Layers menu. The backend (`planetarium:get_stack_pictures`) gives each picture (the color picture when there is one, else the stack's preview) with where a 17 x 17 grid of picture points lies on the sky, from the stretched stack's plate solution including its distortion terms. `layers/StackPictureOverlay.ts` splits the grid into triangles, sends each corner through the map's own projection and draws the picture into each triangle, under the grid, lines, markers and stars. Only pictures in view are loaded (`hooks/useStackPictures.ts`), as a smaller copy unless the view is zoomed in on one. The layer shows in both browse modes while it is on.

## Target markers

`layers/TargetOverlay.ts` marks each library target, and `layers/targetMarkers.ts` picks the marker:

- A target whose picture the *Images* layer draws gets no marker, since the picture shows where it is. When it is the selected target, a thin line traces the picture's edge: the outer points of its sky grid, sent through the map's projection, so the line bends and turns with the picture. Until the picture has loaded, or if it fails to load, the target keeps its marker.
- A target with a catalog shape (major axis, minor axis and position angle) gets an ellipse of that shape. Its direction comes from projecting a point a short way along the position angle, so it stays right when the sky turns on screen. The library records only the major axis for now (`majorAxisArcmin`, SIMBAD's size, from `planetarium:get_targets`), so no target has a shape yet.
- Any other target gets a small, faint ring of fixed size. When it is selected, four corner brackets frame the object's size: its catalog major axis, else its field of view. The brackets and the ellipse are never smaller than 16 px, so the selected target shows at any zoom.

The selected target gets no name label, because its panel is open beside the map. The colors are the `--sky-target-marker` (faint) and `--sky-target-marker-selected` (bright) tokens in `theme.css`, read through `utils/themeColor.ts`. A click picks a target anywhere within the size its marker is drawn at (`utils/hitTesting.ts`); a star under the click wins.

## Tracking view

With tracking on (the `t` key, or a list pick that asks for it), the map follows one point of the sky, the picked object's RA and Dec. The camera eases to a new point once. After it arrives, the map sets the camera each frame from the same clock reading that draws the stars, so the stars stay still on screen while the horizon and the other alt-az overlays drift (`utils/trackingView.ts`). Only a different point makes the camera move again.

## Smooth sky motion

The map reads the clock on every animation frame, so the sky turns smoothly, even at a 0.1° view where one second of sky turning is about 15 arcseconds. While time plays, the offset is carried forward between the 100 ms playback steps (`hooks/useTimeController.ts`). Only the cheap projection runs on each frame. Catalog queries, visibility and labels do not. The map draws a frame again only when the sky has turned by more than a quarter of a pixel since the last draw (`utils/skyMotion.ts`). A wide view therefore idles (a draw every few seconds), and a tight view draws almost every frame. In tracking mode the camera turns with the sky, so it gets the same quarter-pixel tolerance.

## Package actions

The Planetarium sends two things to the observation packages. Both work on the package of the chosen target (`hooks/useTargetPackage.ts`).

- *Add to observation package* is a button on the information card. It looks for the target's planned package, then its suggested package. If it finds neither, the library creates an empty package (`plan:create_package`). For a catalog star that is not in the library yet, the library first creates the target from its catalog name. If the library cannot find the name, it refuses, and the card shows the reason under the button.
- *Send framing to package* is a button in the Framing panel. It stores the center, the rotation and the panels in the target's package.

## Framing panel

The Framing button on the selected object's card opens a panel for it, under the card in the same column, so the two never cover each other. You set Rows, Columns, Overlap (%) and a rotation. The panel asks the library for the panel centers and corners (`mosaic:preview`) and draws each panel's outline on the map. The rotation is in degrees east of north. The words that explain the rotation come from the library and sit behind the ⓘ button beside the Rotation box. The Planetarium does no framing math. Send framing to package stays off, with its reason shown, until a target is chosen and the library has returned panels.

## Device commands

Slew Telescope Here, in the right-click menu, is a device command. It is greyed, with a Why unavailable? button, while Ekos controls the equipment (`ui/common/hooks/useCommandAvailability.ts`). With the simulated observatory it is on, because the commands reach only simulated devices. A failed slew shows one error message.

## Shared time and target

The current target and the planning time follow the backend's handoff state, so a second Planetarium window shows the same target and time (`ui/common/context/HandoffContext.tsx`). Choosing a time in the Date & Time dialog, or clicking an alignment point, sets the shared planning time.

For exact behavior, read the code.
