# Backend services

This folder holds the backend's services. A service is a Python class that keeps some state or does some work for the app. The RPC route (`/api/rpc`) calls a service method for each request. A service never imports the container (`backend/container.py`) or the routers. The container builds the services and passes each one what it needs. `Container.shutdown_resources()` stops them when the backend stops: the maintenance thread, the observatory control, and every Siril process (through the library's `processing.stop_stacking_programs()`; the backend's exit runs no `atexit` handlers).

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Folders

- `analysis/` runs the analysis jobs.
- `data/` serves targets, stars, images and the deletion archive.
- `infrastructure/` holds shared plumbing: settings, sockets, the scripting terminal, the handoff state and the safety guards.
- `observatory/` serves the telescope, equipment, guiding, imaging, planning and the sequencer. `observatory_source_service.py` runs the Observatory source setting: with a simulator source it starts and stops the stand-in INDI server, PHD2 and driver of `wayfindinglib.simulation`, and, once the simulator is up, a ready hook (the container's `_take_command_of_simulator`) first records the simulator's guider calibration and focus model, then puts the app in controller mode so it can command the stand-in devices (never for the real telescope or the offline world), and a source change saves the choice and restarts the backend (see `backend/observatory_source.py`, which picks the simulator's separate data folders before the settings load).
- `processing/` serves ingestion, stacking and image processing, jobs, the frame quality measurement run and the slow stack reviews (`stack_review_service.py`, which starts a preview remake or a stack comparison as a background job and answers with the result or the job to follow).

## Planning services (`observatory/`)

- `plan_service.py` wraps the planning library's plan and package calls for the RPC route. After each write that succeeds, it sends `plan:changed` (session writes) or `packages:changed` (package writes) to every app window. A write from an old revision stores nothing and raises a conflict error that carries the current revision.
- `advisory_service.py` serves the quality advice and night history reads. It only reads. It does no ranking, grouping or sorting.
- `suggestion_refresh_worker.py` is described below.

## Suggestion refresh worker

The planning library keeps a set of suggested observation packages. It creates, updates and deletes them, adds calibration items to tonight's checklist and marks finished packages complete. That work (`refresh_suggestions`) reads the quality numbers, so it must run when they change. `SuggestionRefreshWorker` runs it in the background.

1. A trigger calls `request`. Triggers come after a frame quality measurement run, after a frame sync and from the Refresh suggestions button. A trigger names the targets whose data changed, or none, which means refresh everything.
2. `request` records the targets and starts one background thread if none is running. It never waits and never raises, so it cannot break the announcement that called it.
3. The thread takes everything recorded so far, runs one refresh and looks again. Triggers that arrived during the run get one more run. At most one run is going and one is waiting, however many triggers come.
4. A run that failed is logged, and the worker goes on.
5. After a run that changed something, the worker sends `packages:changed` with the kind `refreshed` and the counts. If the run added checklist items, it also sends `plan:changed` for tonight's session.

## Heavy read limiter

Every RPC call runs on one of a fixed set of worker threads. A slow whole-catalog read that the client has given up on keeps its thread until it finishes. A burst of retries could take every worker and block even a simple status call. `HeavyReadLimiter` (`infrastructure/heavy_read_limiter.py`) stops that in two ways:

- Only a few heavy reads run at the same time. A read that cannot get a place within a short wait fails at once with a retryable "busy, try again" conflict error.
- Identical requests that arrive together share one computation. A later request waits for the answer of the one already running.

The star catalog listings and counts use it (`data/stellar_service.py`). The numbers (reads at once, wait times) are constants at the top of the module. The per-target star counts and class counts are kept until the catalog changes, but an empty answer is never kept: the library may not have been filled yet at start-up, and a kept empty answer showed "0 stars" for every target until it expired. They are sized so that most of the workers stay free and so that a wait ends before the client's own time limit.

## Heavy lane for RPC methods

Python lets only one thread run Python code at a time. When a view opens and a dozen calls that scan the whole library run together, every call, even a light one, waits behind them each time it needs a turn. A TPM run measured calls that should take a millisecond waiting more than a second. `backend/routers/rpc_router.py` lists these scanning methods in `HEAVY_METHODS` (for example `history:nights` and `astronomy:list`). Only `HEAVY_LANE_SLOTS` of them run at once, and the rest wait without taking a worker thread. All other methods run freely. Add a method to the list when a trace shows it busy for more than about 100 ms.

For exact behavior, read the code.

## Measuring new dark and bias frames

The library's rescan of calibration frames does not measure darks and biases, so a new set would stay "Not checked". `ImageProcessingService.measure_new_calibration_frames` starts that measuring (`calibration:measure_frames`, not forced, so only new or changed files are read) as a background job. It is the one place that starts it. Two callers use it: `IngestionService`, after a rescan that added darks or biases, and `SyncService`, after a Bias, Dark or Flat folder sync that filed darks or biases. Adding only lights or flats starts nothing.

Only one such job runs at a time. A request that arrives while one runs is remembered, and the job starts one more pass when it ends, so late frames are not missed. The job writes its own log in the job list. When a pass ends, the service sends `calibration:measured`, and the Calibration Library panel reads its health again.
