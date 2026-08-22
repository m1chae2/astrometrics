# targetDisplay

This folder implements the "Image Viewer" / Target Manager UI. It follows a
three-column layout:

Left column: `TargetListManager` — shows available processed targets and lets the
  user select one. When a selection is requested it sets `pendingTarget` on the
  parent coordinator component.

Center column: `TargetViewerManager` — displays the processed image. The
  parent (`TargetDisplay.tsx`) orchestrates fetching the processed image data
  (via `src/services/backendApi.ts`), and passes the image info to the viewer.

Right column: `TargetDetailsManager` — shows metadata and actions (analyze,
  stack, download). Actions here generally call backend endpoints and may
  update the processed image when complete.

Data flow summary

1. User selects an item in the list.
2. `TargetListManager` sets `pendingTarget` in the parent.
3. `TargetDisplay.tsx` observes `pendingTarget`, calls the backend service,
   and manages caching of image URLs for the session.
4. `TargetViewerManager` receives the `imageUrl` and renders the image.

Tips for exploring the code

- Start with `TargetDisplay.tsx` to see the fetch/caching orchestration.
- Inspect `src/services/backendApi.ts` for the exact backend endpoints used.
- The list manager and viewer are modular so you can run them independently in
  tests or storybook-like harnesses.

Common extension points

- Add new viewer features by modifying `TargetViewerManager` — it receives an
  `imageUrl` and can support overlays, zoom, and annotation layers.
- To add backend actions, add a helper in `src/services/backendApi.ts` and call
  it from the appropriate manager component.
