# Desktop app architecture

*Version 2.1 · 2026-10-25 · Status: current*

This page explains how the Astrometrics desktop app is built. It is for developers who want to change the app and for readers who want to know how its parts fit together. To use the app, read [Getting started](../../Getting_Started.md) and the [user manual](../user_guides/User_Manual.md).

## How the parts fit

The desktop app has three parts that run as separate programs.

| Part | Built with | Folder | Job |
| --- | --- | --- | --- |
| Electron shell | Electron, a toolkit that turns a web page into a desktop app | `electron/` | Starts the backend, opens the windows, and talks to the operating system |
| User interface | React, TypeScript and Vite | `ui/` | Draws every screen and handles clicks |
| Backend | Python and FastAPI | `backend/` | Does the real work: image processing, the database, and telescope control |

The shell and the user interface follow one rule. They hold no science code. Every calculation and every hardware command goes to the backend, and the backend hands it to the two Python libraries, `astrometricslib` and `wayfindinglib`. The user interface shows the result.

This split has two reasons. The same backend also serves the AI-agent tools and a paired phone, so the logic exists once. And the user interface cannot break a calculation, because it never runs one.

## Layers and their rules

Each layer may use the layers below it and nothing else. Tests and linters enforce the rules, so a wrong import fails the build.

| Layer | Folders | What it may not do |
| --- | --- | --- |
| Domain libraries | `astrometricslib/`, `wayfindinglib/` | Import `backend` or `mcp_servers`. `astrometricslib` never imports `wayfindinglib`. |
| Services | `backend/services/` | Import `backend.container` or `backend.routers`. |
| Delivery | `backend/routers/` | Hold business logic. Routers only register routes and convert data. |
| MCP servers | `mcp_servers/` | Use anything but the libraries' public API. The backend never imports them. |
| Frontend | `ui/`, `electron/` | Reach the backend any way other than its public interface. |

The rest of this page covers the frontend layer. The import rules above are checked by the import-linter contracts in `pyproject.toml`. The frontend rules are checked by ESLint (`eslint.config.js`) and by backend tests.

## The Electron shell

The shell is the *main process*: the one Node.js program that owns the app. Its files in `electron/` each do one job.

| File | Job |
| --- | --- |
| `main.js` | Starts the app, creates the windows and the tray icon, and handles shutdown |
| `backend_manager.js` | Starts the Python backend, waits until it is ready, and stops it on exit |
| `backend_client.js` | The only file that sends requests to the backend (see the next section) |
| `preload.js` | Defines the small set of functions a window may call in the shell |
| `ipc_handlers.js` | Answers those calls in the main process |
| `figure_target.js` | Decides what a plot window may open |
| `python_terminal_manager.js` | Runs the Python terminal, and pauses it when the screen locks |
| `tray_window.js`, `window_state.js` | The tray popover, and saved window size and position |
| `platforms/` | The differences between Linux, macOS and Windows |

### Start-up

1. `main.js` shows a small splash window at once.
2. `backend_manager.js` starts the backend. For a development run it uses `.venv/bin/python3 -m backend.main_backend`. For a packaged build it uses the built `backend` program. If the environment variable `SKIP_BACKEND` is set, it starts nothing. `build/linux/run_astrometrics.sh` sets it, because that script starts the backend itself.
3. The shell asks the backend's `/api/ready` route until it answers. The backend reports ready after it loads the star catalog.
4. The shell opens the main window, hidden. The window loads the Vite development server in a development run, or the built `dist/index.html` otherwise.
5. Each view tells the shell when its first data has loaded. When all have, the shell closes the splash window and shows the main window. A time limit keeps a stuck view from hiding the window forever.

The backend listens on port 5000 unless `ASTROMETRICS_PORT` names another port.

### Windows

- **Splash window.** Shows `electron/splash.html` during start-up.
- **Main window.** Holds the whole app. Every view is mounted at start-up.
- **Display windows.** Each view can open in its own window, for a second monitor. The URL carries `?mode=<view>&windowId=<number>`, and the window shows only that view.
- **Figure windows.** Hold a Matplotlib plot from the Python terminal. `figure_target.js` lets a figure window open only the backend's own `/figure/<number>` page or a PNG file in the backend's scratch folder. The backend sends each figure's size in pixels, and the window opens big enough for the figure and its toolbar, but no bigger than the screen.
- **Tray popover.** A small window beside the tray icon. It is a second page of the same build, `ui/tray_popover.html`, with its code in `ui/trayPopover/`.

### The bridge between a window and the shell

A window runs web code with Node integration off and context isolation on, so that code cannot reach files or processes. `preload.js` exposes one object, `window.astrometrics`, with a short list of functions in groups such as `app`, `window`, `backend`, `dialog`, `tray` and `terminal`. Each function sends a named message to `ipc_handlers.js`. Examples are opening a file dialog, minimizing a window, and fetching the session token.

## The React user interface

`ui/main.tsx` mounts `App` inside an error boundary and a toast provider. `ui/App.tsx` builds the layout: a title bar, a status header, and a stack of view panels. A panel stays mounted but hidden when you leave its view, so a view keeps its state and its data.

Each view lives in its own folder.

| View | Folder |
| --- | --- |
| Image Viewer | `ui/imageViewerDisplay/` |
| Image Processing | `ui/imageProcessingDisplay/` |
| Command Console | `ui/commandConsole/` |
| Astronomy Manager | `ui/astronomyManager/` |
| Planetarium | `ui/planetariumDisplay/` |
| Observatory Manager | `ui/observatoryManager/` |
| Observation Manager | `ui/observationManager/` |

Other folders hold the title bar (`ui/titleBar/`), the status header with the settings panel (`ui/statusHeader/`) and the tray popover (`ui/trayPopover/`). The list of views and the setting that turns each on or off live in `ui/common/constants/displayFlags.ts`.

`ui/common/` holds everything shared: components, hooks, services, queries, contexts and types. The shared components in `ui/common/components/` are described in [its README](https://github.com/m1chae2/astrometrics/blob/main/ui/common/components/README.md). Its files must not import from any other top-level `ui/` folder. ESLint enforces this, so a shared file never depends on one view.

## Talking to the backend

The user interface and the shell reach the backend only through its public interface. `backend/public_interface.py` is the one list of it. It has two parts.

**RPC methods.** `RPC_METHODS` lists every method that the `/api/rpc` route answers. RPC stands for remote procedure call: the app sends a small JSON message that names a method and its inputs, and gets the result back. Method names have the form `group:action`, such as `target:list` or `telescope:park`. The groups cover system and settings, the Python terminal and docs, guiding, the telescope, the observatory, ingestion, processing, analysis, targets, stars, the planetarium, imaging, images, calibration, mosaics, planning (`plan:` and `advisory:`), sessions and the sequencer.

**Routes.** `ROUTES` lists the few HTTP and WebSocket routes that RPC cannot carry.

| Route | Kind | Purpose |
| --- | --- | --- |
| `/api/rpc` | HTTP | Answers every RPC method |
| `/api/ready` | HTTP | Says whether start-up has finished |
| `/api/session-token` | HTTP | Gives the WebSocket token to the app |
| `/api/pairing-info` | HTTP | Tells a companion phone how to connect |
| `/` | HTTP | Says the backend is running |
| `/ws/events` | WebSocket | Streams live events and telemetry |
| `/ws/terminal` | WebSocket | Runs the plain Python terminal |
| `/ws/figure/{figure_id}` and `/figure/...` | WebSocket and HTTP | Serve interactive Matplotlib figures |
| `/static` | Files | Serves image and FITS files from the library |
| `/mpl_static`, `/_images` | Files | Serve the styles and icons that figure pages load |

### Who may call the backend

Only two files in `ui/` may call `fetch` or open a WebSocket:

- `ui/common/services/backendApi.ts` sends the RPC calls (`callBackend(method, params)`) and fetches image files.
- `ui/common/utils/socketClient.ts` opens and keeps the WebSockets.

Every other file calls those two. ESLint blocks `fetch` and `WebSocket` everywhere else in `ui/`. Tests, test setup files and the `ui/mcp/` tool server are outside this rule.

The shell has the same rule. Only `electron/backend_client.js` may call `fetch` or open a WebSocket. It offers two functions: `callRpc` and `checkReady`. It reads the backend's port from `ASTROMETRICS_PORT`.

The generated file `ui/common/types/backendTypes.ts` lists the same method names and route paths. TypeScript then rejects a call to a method that does not exist. After you change `backend/public_interface.py`, run `build/codegen/generate-types.sh`.

### What one call looks like

1. A view calls a function in a service file, such as `targetService.ts`. That function calls `callBackend("target:list", {})`.
2. `callBackend` posts a JSON-RPC 2.0 message to `/api/rpc`. It shares one request between identical read calls that are in flight together. A call times out after 45 seconds, or 90 seconds for heavy methods.
3. `rpc_router.py` runs the matching service method from the backend container. It registers only methods that `RPC_METHODS` declares.
4. The reply returns, and the service function gives the view typed data.

### Live updates

`socketClient` keeps the `/ws/events` connection open. The backend sends messages of type `UI_EVENT`, each with an action name and a payload. The telescope is polled every two seconds, and the backend sends the whole system state as a `system_state_update` action. Other actions tell the app to switch views (`navigate`), share the selected target between devices (`handoff`), show a log line (`log`), reload the page (`refresh`), or refresh star data (`catalog:changed`). When a session changes, the backend sends `plan:changed`. When an observation package changes, or a suggestion refresh runs, it sends `packages:changed` with the package id, the target id and a kind (after a refresh, the kind is `refreshed` and the payload holds the counts). When a frame measurement job stores new quality numbers, the backend sends `quality:measured` with `{targetId, framesMeasured, rowsWritten, observingNightIds}`, and the app marks the stored quality queries for that target or those nights as stale so open screens read them again (`ui/common/queries/qualityQueryKeys.ts`). `backend/public_interface.py` declares every event in `UI_EVENTS` with a one-line meaning, and `build/codegen/generate-types.sh` copies the list into `ui/common/types/backendTypes.ts`. A test fails if the backend sends an event that is not declared.

Both WebSockets need a session token. The shell makes a random token, passes it to the backend it starts, and hands it to windows over the `backend` bridge. If the shell did not start the backend, the app asks `/api/session-token` instead.

## Planning displays

The Observation Manager, the Observatory Manager, the Planetarium and the Astronomy Manager's star Quality card each show what the planning library returns. They do no ranking, grouping, classification, text writing, unit conversion or joining. Each panel names its source: an RPC method, an event or a library call. If a panel needs a new number, the library gains the call. The panel does not compute it.

### Observation Manager

The Observation Manager (`ui/observationManager/`) keeps a library of observation packages and shows one night's session. The page never scrolls. Panels scroll inside themselves. The display holds one selection: a package from the library or a card from the Execution Queue, never both.

| Panel | Folder file | Data source |
| --- | --- | --- |
| Execution Queue | `ExecutionQueue.tsx`, `ImagingCard.tsx`, `ChecklistDialog.tsx` | `plan:current_session` (the session with the frames captured for each entry, the entry imaging now and its run state), `plan:tick_checklist_item`, and the `sequencer:` methods for the queue itself |
| Observation Packages library | `PackageLibraryPanel.tsx`, `NewPackageSearch.tsx` | `plan:list_packages` (one call for each status: Suggested, Planned, Complete), `plan:create_package`, `plan:edit_package` (Dismiss, Undo, Reopen, Delete and every exposure or dither change), `plan:refresh_suggestions` |
| Package editor | `PackageEditor.tsx`, `ImagePlanner.tsx`, `ImageList.tsx`, `DitherSettings.tsx` | The open package. The draft saves through `plan:edit_package` |
| Visible Targets | `TargetListStatus.tsx` | The targets above the horizon. A click calls the same open-or-create path as New package |
| Last Image | `LastImageViewer.tsx` | The newest captured frame |

Four rules keep the display simple.

- **The library owns the package states.** A package is Suggested, Planned or Complete. The library creates and deletes suggestions, marks a package Complete when enough judged frames arrive, and moves it back to Planned if frames are set aside. The display only sends the user's requests: Dismiss, Undo, Reopen, Delete and edits. Every edit names the revision the window last read, and the library refuses an edit made from an old revision. The display shows the refusal as text and does not retry.
- **One session for each night.** `plan:current_session` returns tonight's session, or nothing before the first package is added. Adding a package to the session makes it Planned.
- **Events refresh the display.** `ui/App.tsx` listens for `plan:changed` and `packages:changed` and marks the session and package queries stale. A refresh result also goes into a small cache entry that the library panel shows as a status line. The session read also repeats once a minute, because frames arrive with no event.
- **No technical text.** A statement shows its plain message only. Confidence uses the register's words: Measured, Partly measured and Tentative.

Sessions are set up by hand here and in Ekos for now. The sequencer (`sequencer:` methods and `backend/services/observatory/target_imaging_executor.py`) stays, because the app is meant to run sessions without Ekos later. Target-level quality is for the library and the AI companion. No display shows it.

The mode names Observation Planner and Observation Execution still map to the Observation Manager (`ui/common/utils/normalizeAppMode.ts`), so links from older settings and agents still open a display.

### Observatory Manager

The Observatory Manager (`ui/observatoryManager/`) holds the equipment setup. Ekos captures for now, so the app never commands a device while Ekos controls it. The rule is in `wayfindinglib`, which refuses every command to a capability that it delegated to Ekos. The display adds two things:

- `useCommandAvailability.ts` (in `ui/common/hooks/`, shared by every display that sends a command) reads the delegation state (`observatory:safety_status`). Until the library says the app may command a capability, the answer is no. The Observation Manager (Start Imaging Session, Test Image) and the Planetarium (Slew Telescope Here) turn off their command controls with the same answer.
- `CommandGate.tsx` turns off the controls that send commands, shows the reason on hover, and adds a Why unavailable? info button (`WhyUnavailableButton`) to the panel title for keyboard and touch users.

Safety Limits (`panels/LimitSettings.tsx`) reads the active telescope (`observatory:get_active_telescope`) and saves through `observatory:set_active_telescope` with a `limits` value. The library checks the limits and refuses to create a configuration section. Camera Profiles (`equipment/CameraProfilesPanel.tsx`) shows the camera noise that the library returns with each profile, and the Measure noise button calls `calibration:measure_sensor_noise`. The Calibration Library (`calibration/CalibrationLibraryPanel.tsx`) reads `calibration:query`.

### Planetarium actions

The Planetarium adds two actions that work on the chosen target's package (`planetariumDisplay/hooks/useTargetPackage.ts`): Add to observation package, and Send framing to package from the Framing panel (`components/FramingPanel.tsx`). Both call `plan:list_packages` first. A planned package wins, then a suggested one, and only then does `plan:create_package` make one. The framing numbers come from `mosaic:preview`. The library also supplies the panel corners and the words that say what the rotation means.

### Shared current target and planning time

Each window is a separate React tree. A shared current target and a shared planning time reach them through the backend's handoff state. `HandoffContext.tsx` reads it at start (`handoff:get_state`), follows the `handoff` event, and sends changes with `handoff:update_state`. `TargetContext` and `PlanningTimeContext` build on it. The Planetarium uses the planning time. Electron can open any display in a second window, placed on another monitor, and the target follows across windows.

### Star Quality card

The Stellar Analysis panel in the Astronomy Manager starts with a Quality card (`ui/common/components/quality/QualityChip.tsx`). It asks `advisory:result` for the statements about one star's result and draws them in the order returned. A statement has a kind word (Result, Warning, Recommendation, Limit or Not enough data), a confidence chip and the plain message. A statement with cited frames has an Evidence button, which opens the cited frames list in the Image Viewer. A row with a picture file has Open image, which draws the frame with `images:convert_fits`; a row without one shows the library's reason (`quality:resolve_rows`). A calibration statement has Show in calibration library. Both come from `useStatementActions`.

## State

State lives in four places, each for a different kind of data.

| Kind | Where | Examples |
| --- | --- | --- |
| Backend data | The backend, copied into TanStack Query caches (`ui/common/queries/`) | The target list, star lists, the execution queue |
| Shared app state | React contexts in `ui/common/context/` | `AstrometricsContext` (settings, health, telescope and job status), `TargetContext` (the selected target) |
| View state | Local React state and hooks inside one view's folder | Sliders, tabs, the open dialog |
| Per-window settings | Browser `localStorage` | The last view, the selected target, the target-list filter, a custom backend address |

Three details help when you read the code.

- **One query cache.** `App.tsx` makes a single query client. Several views that need the target list share one request and one cached result.
- **Events between views.** `ui/common/utils/eventBus.ts` carries small messages between components that do not know each other, such as "the target list changed". Window events named `astrometrics:...` do the same across the whole page.
- **Storage may fail.** Every `localStorage` call sits in a `try` block, because a private window or blocked site data makes it throw. The app must work without it.

The backend is the source of truth. A view never keeps its own copy of the science data, only a cache of what the backend last said.

## Where to look in the code

| To change or find | Look in |
| --- | --- |
| Which RPC methods and routes exist | `backend/public_interface.py` |
| How an RPC method is handled | `backend/routers/rpc_router.py`, then the service in `backend/services/` |
| The WebSocket routes | `backend/routers/websockets.py` |
| Start-up work and the telemetry loop | `backend/startup.py` |
| Where services are created | `backend/container.py` |
| How the app calls the backend | `ui/common/services/backendApi.ts` |
| Live events | `ui/common/utils/socketClient.ts` |
| One service per area | `ui/common/services/` |
| The generated types | `ui/common/types/backendTypes.ts` |
| The list of views | `ui/App.tsx` and `ui/common/constants/displayFlags.ts` |
| How the shell starts and stops the backend | `electron/backend_manager.js` |
| What a window may ask of the shell | `electron/preload.js` and `electron/ipc_handlers.js` |
| The lint rules that guard these boundaries | `eslint.config.js` |

After a change under `ui/`, run `npm run type-check` and `npm test`. After a change to `electron/` or `ui/`, run `npm run lint`.
