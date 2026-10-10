# Asteroid detection

This pipeline searches a sequence of images of the same field for objects that move against the fixed background stars — asteroids and other minor planets. It works by tracking dots of light that shift position, frame to frame, in a way a fixed star never does.

## What the pipeline does, in order

1. **Work out where each frame is pointing.** `frame_wcs_composer.py` estimates a per-frame World Coordinate System (WCS, the mapping from pixel position to sky position) from the mount's reported pointing, since a raw light frame usually has no plate-solved WCS of its own yet.
2. **Find every dot of light in each frame.** The pipeline reuses astrometry's own source detector (`SourceDetector`) to find point sources in every frame of the sequence.
3. **Track dots across frames and filter out noise.** `detection.py`'s `MovingObjectDetector` links up detections of the same moving object across frames and runs a cascade of checks — is it detected in more than one frame, does it move in a straight line at a constant rate, is the rate within a plausible range — rejecting a candidate at whichever check it fails. `models.moving_object.CascadeStage` records how far each candidate made it: a rejected candidate keeps the stage it was rejected at (for example `rejected_stationary_pixel`, a dead or hot pixel that never moved at all), and a surviving candidate reaches `rate_linearity_confirmed`. The next three subsections describe the parts of this step that decide the result.

   Right Ascension (RA, the east-west sky coordinate) wraps from 360 deg back to 0 deg, so a field or a mover can straddle RA = 0 h. The detector sends every angular difference in RA, both the chaining search box and the offsets in the stationary and rate checks, through the shared helper `wrapped_ra_difference_deg` in `detection.py`. The pointing correction in `pipeline.py` does the same: it measures every reference star and detection as a wrapped offset from one centre RA before it builds its search tree. The helper returns the short way around the circle, in the range (-180, 180] deg. New code in this package must use it instead of subtracting RA values.

   **Chaining runs inside one observing night.** The detector groups detections by observing night (`observing_night_id` in `utilities/observing_night.py`; a night runs from local noon to local noon) and links dots within each night on its own. Within a night, a track may add a dot only if the dot lies within a match radius of the track's last dot. That radius is `rate_max_arcsec_per_hour` times the elapsed hours, capped at `chain_match_radius_max_arcsec` (default 300 arcsec, 5 arcminutes). Without the night split, a gap of weeks let the radius grow to 1 degree, and two unrelated stars half a degree apart, seen on nights a month apart, linked into a false mover at 2.5 arcsec per hour. Joining a mover that appears on more than one night is a separate step that does not exist yet. A real asteroid seen on two nights comes out as two separate candidates. The detector knows only each frame's time, so a "session" here is an observing night; it does not split by camera gain or offset the way `derive_target_sessions` does.

   **The straight-line test uses residuals in arcseconds.** The detector fits a straight line to the sky position against time, separately for RA and Dec. It then takes the root-mean-square (RMS) distance of the dots from that line on each axis. The track passes when both RMS values are at most `residual_rms_max_multiple` (default 2) times the position error of one picture, and when the rate is inside the allowed range. The position error of one picture comes from the pointing-correction step in `pipeline.py`. After it shifts a frame so its stars line up with the stack's stars, it measures how far the stars still scatter (a robust standard deviation of the nearest-star residuals). It adds `centroid_error_px` (default 0.5 pixel) in quadrature, which is the square root of the sum of squares, to allow for the error of finding a faint dot's centre. The result is stored on each detection as `astrometric_error_arcsec`. When a frame has too few matched stars to measure it, the detection carries no value and the test uses `astrometric_error_default_arcsec` (default 10 arcsec). The track records which case applied in `astrometric_error_assumed`.

   A third condition guards slow movers. The fitted line must carry the object at least `min_displacement_error_multiple` (default 3) times the position error between the first and the last picture. Without it, a star whose measured position jitters by its own error would pass the residual test, because a flat scatter fits a line with small residuals. Set the value to 0 to switch this condition off.

   A fourth condition rejects a chain that moves back and forth. The detector projects each dot onto the direction of the fitted motion and checks the projections in time order. A real mover only goes forward along that direction. For every pair of dots, the earlier one may lie ahead of the later one by at most the combined position error of the two (the square root of the sum of their squared errors). A chain that fails this check, and meets every other condition, gets the stage `rejected_non_monotonic`. This catches a star whose measured centre flips between two points: its positions fit a line with small residuals, but they alternate along it. A chain that fails an earlier condition keeps that condition's stage, so the stage `rejected_non_monotonic` counts only chains that this check alone rejected.

   R-squared (how much of the motion a straight line explains, from 0 to 1) is still computed and stored as `linear_fit_r_squared`. It does not decide anything: any chain with a large displacement scores near 1, even when its dots miss the line by many arcseconds. A passing track also stores `residual_rms_right_ascension_arcsec`, `residual_rms_declination_arcsec`, `astrometric_error_arcsec` and `residual_limit_arcsec`. A rejected candidate keeps no track, so its residuals are not stored; the counts of tested and rejected chains go into the run metrics instead.
4. **Check survivors against known asteroids.** `ephemeris.py`'s `EphemerisCrossMatcher` asks an external ephemeris service (SkyBoT) which known minor planets are near a spot of the sky at a given time. For each surviving candidate it asks twice: at the time and position of the candidate's first detection, and at the time and position of its last detection. A known asteroid matches only if it lies within `ephemeris_cross_match_radius_arcsec` (default 10 arcsec) of the candidate at both moments. A match reaches `ephemeris_matched` and stores the two separations in `first_detection_separation_arcsec` and `last_detection_separation_arcsec` (`angular_separation_arcsec` holds the larger one). An unmatched survivor stays at `rate_linearity_confirmed` — a real moving object, but not yet identified as a specific known body. The matcher remembers each answer within a run, waits at least half a second between questions that go out over the network, and skips the second question when the first one fails. Positions are the measured detection positions, not the fitted line, so the 10 arcsec radius sits close to the position error of a single frame (about 5 to 13 arcsec after the pointing correction); a real match can miss at one end.
5. **Look for a rotation period, when enough data exists.** `rotation_period.py` searches a tracked object's brightness history for a repeating pattern, the way an asteroid's periodic brightness variation reveals its rotation.

## Where each piece lives

- `pipeline.py` is the coordinator: it ties together WCS estimation, detection, the rejection cascade, and ephemeris matching for one run.
- `runner.py` sits one level above `pipeline.py`. It runs the pipeline across a target's stored images and writes the results back onto the target's record.
- `detection.py`, `ephemeris.py`, `frame_wcs_composer.py`, `rotation_period.py` each implement one step above.
- `mpc_report.py` writes detections as Minor Planet Center (MPC) 80-column observation lines. Its functions only turn numbers into text; no other module in the package calls them yet. See "MPC report lines" below.

## A note on scope

This pipeline finds and classifies moving objects; it does not stack, plate-solve, or catalog-identify the fixed stars in the same images — it depends on astrometry's per-frame star detection but does not duplicate astrometry's own plate-solving.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.

## The gate record

The summary keeps one record per check in `gates`, built in `run_gates.py`. Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. A search that finds nothing says little unless it could have found something, so the first gate records whether it could. The tests in `test/test_run_gates.py` give each gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `search_frames` | Never | Fewer frames could be searched than a track needs (`min_frames_for_persistence`), so no mover could have been found |
| `pointing_metadata` | A frame lacks RA, Dec or image size in its header | No frame was considered |
| `ephemeris_cross_match` | The known-asteroid database (SkyBoT) could not be reached | No mover was confirmed, or the database was never asked |
| `unmatched_movers` | A mover moved in a straight line but matched no known asteroid (worth a manual look) | No mover was confirmed |
| `track_residuals` | A chain that reached the straight-line test had a detection with no measured position error, so the assumed `astrometric_error_default_arcsec` stood in for it | No chain reached the straight-line test |

A failed SkyBoT query used to look the same as a field with no known asteroids. The pipeline now counts the queries it makes and the ones that fail (`ephemerisQueriesAttempted`, `ephemerisQueriesFailed` in the metrics). Each confirmed mover costs up to two queries. Two things are not yet covered: the false-mover rate of the rejection cascade and its recovery of injected movers (Gap 2), and whether the field is near the ecliptic enough for a real asteroid to appear. `NGC 2403` and `M 81` sit far from it, so a clean run there only confirms "found nothing".

When `track_residuals` passes, its measured value is the largest ratio of RMS residual to position error among the accepted tracks, and its limit is `residual_rms_max_multiple`. The detail line also gives how many chains were tested, how many the residual test rejected, and how many were rejected for moving back along their direction. The run metrics carry the same counts as `residual_chains_tested`, `residual_chains_rejected` and `residual_chains_non_monotonic`.

## MPC report lines

`mpc_report.py` builds the classic 80-column MPC observation line: designation, UTC date to 1e-5 day, RA as `HH MM SS.ss`, Dec as `sDD MM SS.s`, an optional magnitude and band, and the MPC observatory code. The date is the middle of the exposure: `DATE-OBS` (the moment the shutter opened, stored as the detection's `timestamp`) plus half of `EXPTIME` (stored as `exposure_seconds`). `mpc_lines_for_candidate` writes one line per detection and also returns how many lines used the exposure start because the frame had no `EXPTIME`; those times are early by half an exposure.

Limits of the writer:

- The designation is written as given. The writer does not convert a name like `2003 XY99` to the MPC's packed form.
- The detections carry an instrumental flux, not a calibrated magnitude, so `mpc_lines_for_candidate` leaves the magnitude and band columns blank.
- ADES, the newer MPC format, is not provided.
- Nothing in the pipeline submits a report. The functions are available for a caller to use.

## Limits of this pipeline

- **No cross-night linking.** A mover seen on two or more nights is reported as separate candidates, one per night. Linking them needs an orbit-style fit over days, which this package does not do.
- **Short sequences only.** The chaining radius is capped at 5 arcminutes, so a fast mover (300 arcsec per hour) is followed across gaps of up to about one hour. Raise `chain_match_radius_max_arcsec` for longer gaps within a night, at the cost of more false links.
- **Slow movers need a long baseline.** The displacement condition means an object must travel about three times the position error between its first and last picture. With the assumed 10 arcsec error, that is 30 arcsec.
- **A reversal smaller than the position error passes.** A star that flips by less than the combined error of two dots is not caught by the direction check.
- **The measured position error reads low when it is large.** The estimate ignores stars farther than 15 arcsec from their nearest reference star, so a frame whose true scatter approaches that value is under-measured, and the test then allows less than intended.
- **Mid-exposure times need `EXPTIME`.** A frame without it gets the exposure start, and rates and reports are early by half an exposure.
- **The `track_residuals` limits are not yet validated on real fields.** The synthetic fields share one pointing and have no position error to speak of.

## What the detector can find

Measured on 2026-10-10 (`scripts/validate_asteroid_detection.py --synthetic`, with the SkyBoT query stubbed out so the run is offline; synthetic fields of 8 frames 300 s apart at 1.8 arcsec per pixel, with a mover of the given peak brightness above the sky noise, 12 fields per cell):

| peak brightness | 5 arcsec/h | 20 arcsec/h | 60 arcsec/h | 150 arcsec/h |
|---|---|---|---|---|
| 3 sigma | 0% | 0% | 0% | 0% |
| 5 sigma | 0% | 0% | 0% | 8% |
| 8 sigma | 0% | 83% | 83% | 92% |
| 12 sigma | 0% | 67% | 83% | 92% |
| 20 sigma | 0% | 83% | 92% | 100% |

Two limits show. A mover is not found below about 8 sigma at its peak. And a slow mover is not found at any brightness: at 5 arcsec/h an object moves only 1.6 pixels in the 40 minutes of this sequence, less than the 1.5 pixel tolerance within which a track is called stationary, so it is rejected as a star.

In 40 synthetic fields with no mover, one track was confirmed by the residual test alone. It was a star whose measured centre flipped between two positions 2.8 pixels apart. The positions fit a line to within 1.8 arcsec (the limit here, twice the 0.9 arcsec position error of these frames), so the residual test passed it, although R-squared was only 0.40. The R-squared test that the residual test replaced rejected that track, and confirmed none in the same 40 fields. The direction check now rejects that field (it reports `rejected_non_monotonic`); the other 39 fields and the recovery table above were not re-run after the check was added. The synthetic frames share one pointing, so these numbers test detection and chaining, not the error in each real frame's position.

The counts on real fields predate the residual test and the per-night chaining and have not been repeated since. On frames of targets far from the ecliptic, where an unmatched confirmed mover is almost certainly not an asteroid, NGC 2403 (35 frames) gave one confirmed mover that matched no known asteroid and M 101 (40 frames) gave none.
