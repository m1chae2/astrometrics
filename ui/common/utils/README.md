# Utilities

This folder contains small cross-cutting utilities used by the UI.

## Key Modules

- **eventBus.ts**: A simple, typed event bus for cross-component communication.
- **presence.ts**: Says whether the user is looking at the app (`present` or `away`). Only desktop notifications use it. The desktop app supplies the answer; a plain browser uses page focus and visibility.
- **emitToast.ts**: Helper to emit toast notifications from anywhere (React or non-React). A toast reaches the desktop only when the caller passes `{ notify: true }` and start-up is over. Even then, a desktop notification goes out only when the user is away from the app (see `presence.ts`); when they are looking at it, the toast is enough. A `notify` toast also stays on screen until the user closes it, and a toast can carry an `action` button such as "Open". An away alert stays until dismissed, and the main process closes it when the user returns.
- **reportError.ts**: Centralized error reporting (logs to console and emits toasts).
- **ToastProvider.tsx**: Global React provider for the toast system.
- **ConfirmDialog.tsx**: Reusable confirmation modal.
- **plotTools.ts**: Shared physics and plotting utilities.
- **dataUrl.ts**: Decodes a `data:` URL (a picture sent as text) into a Blob. The app's security policy blocks `fetch` on `data:` URLs, so use this instead.
- **starDisplayFormat.ts**: Turns a star's raw data into short text, such as list labels, coordinates and light-curve axis titles. The Astronomy Manager, the planetarium and the FITS viewer's star labels all use it.
- **displayHandoffStore.ts**: The one place where a "show this in that display" request waits until the display can act on it. See "Hidden displays and hand-offs" below.
- **commandConsoleEvents.ts**: The name and shape of the events the Command Console receives through the hand-off store (what an AI agent ran in the terminal, a plot it made, an editor change).
- **targetsUpdatedVersion.ts**: Counts the `targetsUpdated` events, so a display that was paused can tell that its cache is out of date.
- **socketClient.ts**: The app's WebSocket connections to the backend. `socketClient` keeps the `/ws/events` connection open and turns its messages into events. `openTerminalSocket` opens the Python terminal's `/ws/terminal` connection.

## Hidden displays and hand-offs

The app keeps every display mounted and hides the ones you are not looking at. A hidden display is also paused (React's `Activity`; the list is `PAUSABLE_MODES` in `ui/App.tsx`). A paused display keeps its state, but all of its effects stop: timers, socket listeners, the Planetarium's drawing loop. They start again when the display is shown.

That has one rule for everyone who wants another display to do something. Do not send it a window event, because a paused display is not listening. Post the request to the hand-off store instead:

- `postHandoff(display, kind, payload)` leaves a request for a display. Most kinds keep only the newest request. Pass `{ queue: true }` for a kind that must not lose items, such as agent terminal output; it keeps them in order, up to a limit.
- `useHandoffConsumer(display, kind, handler)` is what the receiving display calls. It hands over each request once, as soon as the display is running, even if the request was posted while the display was hidden or not yet loaded.
- `usePendingHandoff(display, kind)` and `consumeHandoff(request)` are for a display that must wait before acting, such as the Planetarium waiting for its target lists.
- `observeHandoffs(kind, listener)` is for parts of the app that are always running (providers), not for displays.
- `navigateToElement` in `displayCoordinator.ts` posts to the store for you. Use it for "open this in that display".

Two other rules keep a display safe to pause. Work that must go on while the display is hidden does not belong inside it: frame downloads are followed and finished by `IngestionJobsContext`, which is always running. And an effect must not free something the display's state still uses: the Image Viewer's pictures are kept in `imageViewerDisplay/hooks/targetImageCache.ts` for that reason.

## Folder rule

Files under `ui/common/` are shared by the whole app, so they must not import from any other top-level `ui/` folder (such as `ui/astronomyManager/`). When a piece of code is needed both here and in a feature folder, move it into `ui/common/`. An ESLint rule enforces this.

## Talking to the backend

The app may reach only the backend's public interface, which `backend/public_interface.py` declares. Use `callBackend` and `fetchImageFile` in `ui/common/services/backendApi.ts`, or the socket helpers in `socketClient.ts`. These two files are the only ones allowed to call `fetch` or open a WebSocket. An ESLint rule enforces this, and a backend test checks every path they use against the declaration.
