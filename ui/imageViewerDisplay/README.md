# imageViewerDisplay

The selected target is shared by the Image Viewer, Image Processing and Observatory Manager through `TargetContext` and is kept in `localStorage`. `useTargetListLogic` (in `ui/common/hooks/`) picks the default when there is none, or when the kept one is no longer in the library: the most recently imaged target that has a processed image (`pickDefaultTargetId`).

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
  `FitsRenderer` puts the viewer together from hooks in `fitsViewer/hooks/`:
  `useViewPlacement` places the view on each picture (a focus star, a saved view),
  `useStarOverlay` and `StarLabelLayer` draw the star overlay, `useStarMeasurement`
  and `ProfileMarkerLayer` run click-to-measure, and `useDetectionsOverlay` runs
  the Detections overlay.

Pixel readout: the line with the pixel position, value and sky position floats over the
  bottom-left corner of the picture on a translucent dark background (`--surface-overlay`),
  so the picture keeps its full height. It takes no pointer events and shows only while the
  pointer is on the picture. It sits under the star profile panel, so it never hides it.

Star profile: click-to-measure is always on. A click on the picture (not a drag, and not a
  star label or the circle of a named star) measures the star there. A click that finds no star
  (`star_found` is false, for example empty sky) is ignored: the panel does not open, no marker
  is drawn and the pin does not change. If the panel is already open, it keeps the old star while
  the new click is measured. The panel's X only hides it; the next click on a star opens it
  again, and while it is hidden a change of image does not follow the old star. The click is turned
  into a file pixel with the same steps as the pixel readout (`pointerToFileCell` in
  `ui/common/fitsViewer/utils/pixelQuery.ts`), and the FITS worker names the pixel, so the
  click and the readout always agree. The viewer then calls `images:measure_star_profile`
  with that pixel, a search radius of 6 screen pixels (in image pixels at the current zoom, from 3 to 50) so the center snaps to a star near the click, and the path of the file on screen (`resolveProcessedImagePath` in
  `common/services/imaging/measurementPath.ts`). The answer fills `StarProfilePanel`, a
  drawer on the right of the picture with no scrollbar: one line with the star width (FWHM from
  the Moffat fit in pixels and arcseconds, or the Gaussian fit when Moffat failed), a Clipped
  chip when the star is clipped, one bell-curve plot, and an angle slider. The plot shows the
  real pixels along a cut through the star as points, the fitted shape mirrored to both sides
  as a line, and a dashed line at half the peak to mark the width. The answer carries a small piece of the image (the cutout), and the
  browser rebuilds the cut from it (`utils/starCut.ts`), so the dashed line and the points move
  with the angle slider at once, with no call to the backend. A followed star that is not found
  shows the backend's reason in the header in place of the width, with a small neutral marker
  instead of the FWHM ring. A click that finds a star also pins the star (the image's path and the star's 1-based position). When the viewer
  shows another image while the panel is open, the panel calls `images:map_position` from the
  pinned image to the new one and measures the star at the mapped position, with the normal
  snap radius (at least 10 image pixels when the answer is only a guess). The line under the
  header says how it was found: "Followed by sky position", "Followed by stack alignment",
  "Followed by stack alignment and sky position" or "Same pixel (guess)". The tab, cut angle, cut
  length, ring and log settings stay. When the star cannot be followed the panel says why, and
  when the new spot is not a star it shows that; the pin stays on the image that was clicked, so
  switching back finds the star again. A click on another star pins that one instead.

  The panel has two views. "Along the line" is the cut plot. Its Cut length slider
  (20 to 100 pixels) rebuilds the cut from the cutout when the cutout already covers the length;
  a longer cut measures the star again once, after the slider rests (`cut_half_length_px`).
  "Around the star" shows the ring the backend measured (the `ring_*` fields of the answer; the
  measurement is in `astrometricslib/pipelines/shared/quality/star_ring.py`). The browser draws
  it and does no ring analysis of its own; `utils/starRing.ts` only builds the smooth curve, the
  one-line summary and the direction names. The plot shows the mean brightness in 5 degree bins
  of direction, measured like the cut angle, using only the pixels the backend kept (neighbor stars
  are left out). Its axis names the screen directions (Up, Right, Down, Left, which depend on the
  picture's row order) and marks the cut angle and the angle plus 180 degrees. An orange line over
  the points is the constant plus every Fourier term that reached the backend's significance
  threshold. Under the plot, a short bar chart shows the size of each order from 1 to 8 as a percent of
  the ring's mean brightness (noise bias removed), with a 1 sigma error bar; significant bars use
  the accent color. A line under the chart names the strongest significant patterns, for example
  "One-sided 12% ± 2% toward Up; oval 3% ± 1%" (order 1 is one-sided, 2 oval, 3 three-fold, 4
  four-fold, higher "m-fold"; only the one-sided pattern names a direction), or "Round within the
  noise". The picture shows the two ring circles instead of the cut line, and a faint dashed
  circle around each neighbor star that was left out. The ring radii come from the backend: it
  starts where the mean brightness measured in the pixels falls to 1% of the peak and ends at 1.5
  times the radius where it falls to twice the sky noise. The panel shows them as a plain line,
  such as "Ring 4 to 24 px", next to the count of neighbors left out, with no control to change
  them. A spike shows as a peak, and a spike with no
  partner 180 degrees away is one-sided. A caption ("Pixels along the dashed
  line") ties the plot to the dashed cut line on the picture. A Log scale button switches the
  brightness axis to a log scale to show the faint wings (a one-sided flare shows as one tail
  higher than the other); values at or below zero are raised to the sky noise, and the axis
  title says so. While the panel is open, the picture shows the cut line and a ring at the
  center that is one FWHM across (a ring under about 4 screen pixels keeps its size and gets
  a thicker line). A picture that holds no pixel values (Image Processing in Auto Stretch) cannot name a
  pixel, so the panel says so and sends nothing. This viewer shows the stretched
  `_processed.fits` file, so the panel notes that its brightness is not in camera counts;
  the widths are still good.

Detections: the Detections button in the viewer header (here and in the Image Processing FITS
  Viewer) marks every star the astrometry run's star
  finder detects on the image shown. The viewer calls `images:measure_detections` with the file
  path (`useDetections`), so the stars are found now from the pixels, with the same function the
  run uses (`find_unique_stars`), on any image. For a stack's stretched `_processed` picture the
  backend searches the linear stack beside it, as the run does. The backend keeps the answer for the file's
  modified time, so turning the overlay off and on is quick. A single star is a circle and a star
  reported twice and merged is a diamond (a different shape, not only a different color). Each
  circle is the circle photometry measures every star in (`aperture_radius_px`, in image pixels),
  sized from the star width measured on this image (`star_width_px`), so it grows and shrinks with the zoom and shows the light photometry would count. It is the same
  size for every star, because all stars in one image share one shape. Zoomed far out, a circle
  never draws smaller than 3 screen pixels, so it stays visible. The
  colors are the `--detection-marker-color` and `--detection-merged-color` tokens. Positions are
  1-based file pixels placed like the star profile marker (`utils/detectionMarkers.ts`), drawn as
  two SVG paths, and only markers in view are drawn. A note in the corner of the picture says
  "Finding stars…" and then the count. An image the backend refuses (outside the library) shows
  nothing, and a picture with no pixel values shows the same Linear hint as the star profile. The
  overlay is separate from the named-star circles of the astrometry overlay.

Right column: `TargetDetailsManager` — shows metadata and actions (analyze,
  stack, download). Actions here generally call backend endpoints and may
  update the processed image when complete. The Stack action runs the full
  stacking stage (`processing.stacking.run`), the same one the batch script
  runs, so it also saves the quality summary, and then makes the processed
  image (`processing.enhancement.run`).

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
the reason the library gives, as text. The list is asked for with the `showFrames`
hand-off (see `ui/common/utils/README.md`); `citedFramesStore.ts` keeps the request for
as long as the banner shows it.

The viewer's pictures are kept in `hooks/targetImageCache.ts`, not in the display.
The Image Viewer can be paused while it is hidden, and a pause must not free the
picture's URL. The cache is cleared when the library changes (`targetsUpdated`).

Loading, preview and neighbors

While a picture loads, the viewer shows the spinner and keeps its canvas hidden.
`FitsRenderer` does not read status words for this. `useFitsLoader` reports a
`phase` (`idle`, `fetching`, `parsing`, `decoding`, `ready` or `error`), and the
spinner stays until the drawer reports the first real draw. Only `error` replaces
the spinner.

A stacked target's main picture is a large stretched FITS file
(`<stack>_processed.fits`). Next to it the stacking pipeline saves a small JPEG
preview (`<stack>_preview.jpg`). `fetchProcessedImage` starts the preview fetch
together with the FITS fetch and hands the preview over through `onPreview`;
`useTargetImage` returns it as `previewBlob`. `FitsRenderer` draws the preview as
soon as it is decoded, then draws the FITS image over it when that is ready. The
canvas is not cleared between the two, and the zoom is rescaled by the size ratio,
so the picture does not jump. A target with no preview file shows the spinner until
the FITS image is drawn. Star overlays use FITS pixel positions, so they wait for
the full image and are never drawn on the preview.

Black and white or color

A black-and-white stack can also have a color picture, made from the colors of
another stack of the same target (`<stack>_color_preview.jpg`). The target record
names it in `stacking.colorPreviewImage`. `hooks/useColorPreview.ts` reads that
name with `target:get`. Only when the name is set does the viewer header show a
"Black and white" / "Color" switch (`Tabs`). The app never asks for the file
without the name, so a target without a color picture makes no failed request.
The picture is read from the backend's `/static` folders, like the stack preview,
and only while Color is picked. It is kept in `targetImageCache` by path, so a
pause does not free it. The switch starts on "Color", so a target with a color
picture opens in color; "Black and white" is one click away. The choice is kept
while the app runs. A target without a color picture shows the black-and-white
picture whatever the choice is. A frame opened from the Evidence list hides the
switch.

`hooks/useNeighborPrefetch.ts` prepares the two targets directly above and below
the selected one in the list (as filtered and sorted). It starts only when the
selected picture has loaded, the browser is idle (`requestIdleCallback`, or a short
timer where it is missing) and the window is visible. For each neighbor it fetches
the picture into `targetImageCache` and asks the viewer to decode it with
`prefetchDecode`, so the viewer's decoded-image cache (5 entries) already holds it
when the user selects it. Moving the selection, or hiding the window, cancels the
work under way. No other target is ever prefetched: loading every stack would need
about 2 GB.
