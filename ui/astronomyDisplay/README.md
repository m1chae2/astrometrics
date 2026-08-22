# astronomyDisplay

This folder implements the "astronomy Manager" UI. Responsibilities:

- Left column: `astronomyListManager` — lists available astronomy data metadata and
  allows selecting a spectrum to view.
- Center area: `astronomyViewer` — plots the parsed wavelength/flux arrays and
  renders annotations (known lines) when requested.

Key behavior

- The parent component `astronomyDisplay.tsx` manages selection and caching of
  spectra. When a `pendingSpectrum` is selected it calls
  `src/services/backendApi.fetchAstronomyData(name, signal)` which returns a parsed
  JSON object from the backend.
- The display code contains parsing logic to support several possible shapes
  returned by the backend. The parsing block normalizes those shapes into two
  `number[]` arrays (wavelength and flux).

Caching and cancellation

- This module uses a local cache and an `AbortController` signal to cancel
  in-flight fetches when the user selects a different spectrum quickly.

Testing and extension points

- `astronomyViewer.tsx` is focused on rendering and should accept normalized
  `spectrumData` so tests can exercise plotting without needing backend calls.
- To add custom annotations, enhance `astronomyViewer` and pass annotation
  configuration from the manager component.

Tips for exploring the code

- Start with `astronomyDisplay.tsx` to see the parsing logic and caching.
- Inspect `src/services/backendApi.ts` to understand endpoint shapes.
- Use unit tests in `tests/` to validate parsing logic against fixtures.
