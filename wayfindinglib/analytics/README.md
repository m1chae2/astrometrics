# Analytics

This folder holds the pure calculations that `wayfindinglib` runs on data it has already read. Each function takes its inputs as arguments and returns a result. It reads no files and talks to no device, so a test can run it on small made-up data. The task modules under `wayfindinglib/tasks/` do the reading and call these functions.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

The judging of a target's data quality has its own README: [result_quality](result_quality/README.md).

## Planning: packages, suggestions and progress

An *observation package* is the plan for one target: exposure rows (a count, a filter, an exposure time and so on), a dither setting and a framing. An *observation session* is one night's ordered list of packages.

- `frame_counting.py` decides which captured frames count toward an exposure row. One function does this, so four things always agree: whether a package is complete, which rows a later session still has to ask for, the progress on the queue cards and whether a past session ran at all. A frame counts when all of these hold:
  1. It is a light frame taken with the session's camera and telescope.
  2. Its filter matches the row's filter after the names are normalized ("L", "Lum" and "Luminance" are one filter).
  3. Its gain, offset and binning equal the row's. A row that leaves one unset matches any value.
  4. Its exposure time is within 0.5 s of the row's. This is the match width that calibration frames use (Design estimate; see [WAY-D63](../../documentation/library_design/validation/wayfinding.md#design-estimates)).
  5. It is not set aside. The stacker moves cloudy and trailed frames to an `_excluded` folder and drops them from the target record.
  6. It was taken at or after the row's `counted_from` time, so frames from before the row last changed do not count.

  A frame with a quality flag still counts and carries the flag. A frame with no stored quality judgment yet counts as captured but does not make the row met: only judged frames complete it. `TargetFrameReader` is the one place that reads the science library for the frames.
- `session_progress.py` works out how far a night plan got. For each queue entry it reports how many frames were requested and captured, which entry is being imaged now and a run state. Ekos runs the queue, so "imaging now" is read from the frames: an entry is being imaged when its newest counted frame arrived within a short window (Design estimate; see [WAY-D84](../../documentation/library_design/validation/wayfinding.md#design-estimates)). A night is over once the current observing night is a later one. The observing night changes at local noon.
- `package_suggestions.py` holds what the two kinds of package suggestion share: the noise goal, the number of ranking suggestions, the source key, the evidence fingerprint and the reading of a target's noise.
  - A quality rule that asks for more frames becomes an exposure row of a suggested package while the target's noise is above one default goal (Design estimate; see [WAY-D83](../../documentation/library_design/validation/wayfinding.md#design-estimates)).
  - Tonight's ranking puts its top few targets in suggested packages that have no rows (Design estimate; see [WAY-D85](../../documentation/library_design/validation/wayfinding.md#design-estimates)).
  - The *evidence fingerprint* is a short code of the evidence behind a suggestion. A dismissal lasts until the fingerprint changes.
  - The refresh that creates, updates and deletes the packages is in `wayfindinglib/tasks/planning_tasks/suggestion_tasks.py`.

## Session and equipment analysis

- `alignment_sessions.py`, `capture_history.py`, `guide_exposure_ladder.py`, `guide_pulses.py`, `guiding_spectrum.py` and `tracking_risk.py` analyze guiding, alignment and tracking records.
- `performance_envelope.py` and `envelope_assembly.py` build the performance envelope: the range of results the equipment has reached on earlier nights.
- `pointing_model.py` fits the mount's pointing error.

## Measures that these modules produce

- *Frames captured of frames requested* (`session_progress.py`): counts for each queue entry and each light row, with how many captured frames carry a quality flag and how many are not judged yet. The queue cards draw these counts as they are.
- *Run state* (`session_progress.py`): Imaging now, Not imaging, Done, Partly done, Not run or Progress unknown. The user interface shows it as a word.
- *Package completion* (`frame_counting.py`): a package is complete when the judged frames reach the requested count for every light row.

For exact behavior, read the code.
