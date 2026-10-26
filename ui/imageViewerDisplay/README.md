# imageViewerDisplay

This folder implements the "Image Viewer" / Target Manager UI. It follows a
three-column layout:

Left column: `TargetListManager` — shows available processed targets and lets the
  user select one. When a selection is requested it sets `pendingTarget` on the
  parent coordinator component.

Center column: `TargetViewerManager` — displays the processed image. The
  parent (`ImageViewerDisplay.tsx`) orchestrates fetching the processed image data
  (via `src/services/backendApi.ts`), and passes the image info to the viewer.
  The viewer is the same renderer the FITS viewer uses (`FitsRenderer`), so it
  has the same zoom, pan, Fit and 1:1 controls. The stacking pipeline saves the
  processed image as a stretched FITS file (`<stack>_processed.fits`), which the
  viewer shows as it is, without stretching it again. The file's values run
  from 0 (black) to 1 (white), and the viewer draws them on that fixed scale,
  so the sky stays as dark as the pipeline made it. A JPEG or PNG processed
  image is shown the same way. The viewer parts live in `ui/common/fitsViewer/`
  because the Image Processing view uses them too. `FitsViewerManager` picks
  `FitsRenderer` for FITS data and `imageViewer/ImageViewer` for a plain picture.

Right column: `TargetDetailsManager` — shows metadata and actions (analyze,
  stack, download). Actions here generally call backend endpoints and may
  update the processed image when complete. The Stack action runs the full
  stacking stage (`processing.stack`), the same one the batch script runs, so it
  also saves the quality summary and the processed image.

Data flow summary

1. User selects an item in the list.
2. `TargetListManager` sets `pendingTarget` in the parent.
3. `ImageViewerDisplay.tsx` observes `pendingTarget`, calls the backend service,
   and manages caching of image URLs for the session.
4. `TargetViewerManager` receives the `imageUrl` or `imageBlob` and renders the image.

Tips for exploring the code

- Start with `ImageViewerDisplay.tsx` to see the fetch/caching orchestration.
- Inspect `src/services/backendApi.ts` for the exact backend endpoints used.
- The list manager and viewer are modular so you can run them independently in
  tests or storybook-like harnesses.

Common extension points

- Add new viewer features by modifying `TargetViewerManager` — it receives an
  `imageUrl` and can support overlays, zoom, and annotation layers.
- To add backend actions, add a helper in `src/services/backendApi.ts` and call
  it from the appropriate manager component.

Cited frames (Evidence)

`citedFrames/` holds the Evidence list that opens when a quality statement cites
frames. `useCitedFrames.ts` reads the rows with `quality:query` (each row carries its
unit) and their picture files with `quality:resolve_rows`, which also names the row
ids the library no longer has. A row with a file has an Open image button. The button
draws the frame with `images:convert_fits` (`useOpenedFrame.ts`) and the center viewer
shows it until the user picks a target or clears the list. A row without a file shows
the reason the library gives, as text.
