# Models

This folder holds the typed data models (Pydantic classes) that `wayfindinglib` passes between its layers and sends to the app. A model checks its fields when it is made, and it knows how to turn itself into plain JSON. Models hold data. They do no calculation beyond simple checks.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

Quality statements have their own README: [quality](quality/README.md).

## Folders

- `equipment_and_site/` holds the equipment, the site and the calibration records.
- `planning/` holds what Planning stores and returns: observation packages, mosaics and framings, reports and rankings.
- `session/` holds what Control returns about a night: sessions, guiding and capture records, and progress.
- `policy/` holds the delegation and safety policy.
- `quality/` holds the quality statements and rules.

## Models added for the observation packages

- `equipment_and_site/camera_noise.py` carries a camera's gain and read noise, setting by setting. The gain is in electrons per ADU (analog-to-digital unit, one step of the stored pixel value). The read noise is in electrons. Each number has an evidence status in the register's words: Measured, Partly measured or Design estimate. The Observatory Manager shows Design estimate as Tentative. A single number is never Partly measured. Each entry also says where the number came from. The camera profile list returns these entries, and the Observatory Manager shows them.
- `planning/framing.py` holds a `Framing` (a field center, a camera rotation and any mosaic panels), a `MosaicPanel` and a `SkyCorner`. A package and a session each keep a `Framing`. The rotation is in degrees east of north, as Ekos and KStars give it. `ROTATION_CONVENTION_TEXT` states that in words, and the screens show the text as returned.
- `planning/observation_package.py` holds the `ObservationPackage` and its exposure rows. Its status is Planned, Suggested or Complete. Its revision lets the library refuse an edit made from an old copy.
- `planning/suggestion_log.py` holds the suggestion lifecycle log. Each log line says what happened to a suggestion (created, updated, deleted with the cause, dismissed, undismissed, completed, reopened or promoted) and names the evidence behind it. `PackageChange` is what a suggestion refresh returns: the ids of the packages it added, updated, deleted, completed or reverted. The backend uses it to send one `packages:changed` event.
- `session/observation_session.py` holds the `ObservationSession` and its queue entries. A queue entry starts at the soonest time or at a fixed time, and has a status.
- `session/session_progress.py` holds the answer to "how far did the night get". For each queue entry and light row it carries the frames requested and captured, how many carry a quality flag, how many are not judged yet and a typed run state. The session as a whole carries the entry being imaged now and its own run state, including Not run for a night that passed with nothing captured. `wayfindinglib/analytics/frame_counting.py` holds the counting rules. The screen draws the numbers as they are.

For exact behavior, read the code.
