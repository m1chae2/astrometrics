# Target list and its filters

This folder holds the list that lets you pick a target (a sky object such as M 31 or Vega). The Image Viewer, Image Processing, Observatory and Observation screens all show it. The Planetarium and Astronomy Manager screens have their own "By target" lists and do not use the filters described here.

## What each file is for

- `RadioListManager.tsx` — lays out one list panel: an optional filter panel on top, the search box, the list, and an optional panel of action buttons below. It takes the panel as the `topPanel` prop.
- `RadioListFiltering.tsx` — the search box that sits above the list.
- `TargetListFilterPanel.tsx` — the filter panel for the target list. It has two tabs and a sort switch.

## How the filters work

1. The "By catalog" tab picks a catalog: All Targets, Messier Catalog, NGC IC Catalog, or No Image. A screen can also add Stars.
2. The "By camera" tab picks one of the cameras set up in the observatory config, or All Cameras. Each camera shows how many targets it has imaged.
3. The two picks apply together. A line under the tabs names the active filters, for example `Messier Catalog · ZWO ASI 533MM Pro`.
4. The sort switch orders the list by name (A–Z, with numbers sorted as numbers, so M 2 comes before M 10) or by the most recently imaged (Newest). Under Newest, each row shows the date of its latest frame. When a camera is picked, that date is the camera's latest frame. Targets with no frames go last.
5. The search box narrows the list further by name.

A target must have a processed or stacked image to appear in any catalog except No Image, which shows the targets that lack one.

The catalog, camera and sort picks are shared by every screen and saved between sessions. If a saved camera is no longer in the config, the list falls back to All Cameras. A target you have selected stays selected even when the filters hide it.

## Where the data comes from

- `ui/common/hooks/useTargetListLogic.ts` loads the targets and applies the filters.
- `ui/common/hooks/targetListFiltering.ts` holds the rules themselves (catalog match, camera match, sort), so they can be tested without the screen.
- `ui/common/state/targetListFilterStore.ts` keeps the saved picks.
- The backend method `target:get_camera_index` supplies the camera counts and frame times. It matches the camera name in each frame's header (for example `Nikon DSLR DSC D5300`) to the configured camera name (`Nikon D5300`). If the request fails, the list still works and shows no camera data.

For exact behavior, read the code.
