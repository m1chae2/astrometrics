# Backend services

This folder holds the backend's services. A service is a Python class that keeps some state or does some work for the app. The RPC route (`/api/rpc`) calls a service method for each request. A service never imports the container (`backend/container.py`) or the routers. The container builds the services and passes each one what it needs.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Folders

- `analysis/` runs the analysis jobs.
- `data/` serves targets, stars, images and the deletion archive.
- `infrastructure/` holds shared plumbing: settings, sockets, the scripting terminal, the handoff state and the safety guards.
- `observatory/` serves the telescope, equipment, guiding, imaging, planning and the sequencer.
- `processing/` serves ingestion, stacking and image processing, jobs and the frame quality measurement run.

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

The star catalog listings and counts use it (`data/stellar_service.py`). The numbers (reads at once, wait times) are constants at the top of the module. They are sized so that most of the workers stay free and so that a wait ends before the client's own time limit.

For exact behavior, read the code.
