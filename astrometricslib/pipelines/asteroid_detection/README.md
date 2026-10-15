# Asteroid detection

This pipeline searches a sequence of images of the same field for objects that move against the fixed background stars — asteroids and other minor planets (small bodies that orbit the Sun: asteroids, comets and dwarf planets). It works by tracking dots of light that shift position, frame to frame, in a way a fixed star never does. A confirmed mover that no known asteroid explains is worth a person's look, and a matched one gives positions that can be reported to the Minor Planet Center.

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This README also defines each term the first time it appears. The terms used most here:

- **Sky coordinates.** Right Ascension (RA) is the east-west sky coordinate, like longitude; declination (Dec) is the north-south coordinate, like latitude. Small angles are given in arcminutes (1/60 degree) and arcseconds (arcsec, 1/3600 degree). A mover's rate is given in arcseconds per hour.
- **Plate solving** works out where in the sky an image points by matching its stars to a star catalogue. Its result is a **WCS** (defined in step 1).
- **Ephemeris**: a table of where a known solar-system body is on the sky at a given time. **SkyBoT** is an online ephemeris service that answers "which known minor planets are near this spot at this time".
- **MPC** (Minor Planet Center): the international clearing house that collects asteroid position measurements.
- **Position error**: how far a measured position may be from the true one, in arcseconds; the pipeline measures it for every frame (step 3).

## What the pipeline does, in order

### Step 1. Work out where each frame is pointing

`frame_wcs_composer.py` estimates a per-frame World Coordinate System (WCS, the mapping from pixel position to sky position) from the mount's reported pointing, since a raw light frame usually has no plate-solved WCS of its own yet. It takes the rotation and scale from the stack's solved WCS and centres it on the RA and Dec the mount wrote into the frame's header. An amateur mount's reported pointing is typically off by tens of arcseconds to a few arcminutes, so step 3 corrects it.

### Step 2. Find every dot of light in each frame

The pipeline reuses astrometry's own source detector (`SourceDetector`) to find point sources in every frame of the sequence.

### Step 3. Correct each frame's pointing and measure its position error

**Why.** Left uncorrected, the mount's pointing error makes every fixed star in a frame appear to move from one frame to the next, which is exactly what the later checks try to screen out.

**What it does.** `pipeline.py` detects the stack's own stars (the reference stars, whose positions come from the stack's plate solution). For each frame it then finds the one shift in RA and Dec that lines most of the frame's dots up with those stars, and applies it to every detection in the frame. It finds the shift by voting: it collects the offset of every dot to every reference star within 300 arcseconds and takes the offset that most pairs share (5 arcsecond bins), so the many wrong pairs in a dense field do not pull the answer. A frame with fewer than 5 votes for the winning offset keeps its uncorrected mount pointing. The search radius and bin width are partly measured (see [MOV-05](../../../documentation/library_design/validation/asteroid_detection.md#mov-05)); the vote count is a design estimate.

**The position error.** The position error of one picture comes from the same correction:

- After it shifts a frame so its stars line up with the stack's stars, it measures how far the stars still scatter on each sky axis (a robust standard deviation — a spread estimate that a few wild values cannot inflate — of the nearest-star residuals, east-west and north-south), and the correlation between the two axes' residuals (`PositionError`). The east-west distance is the RA difference times the cosine of the declination, because lines of equal RA meet at the poles, so one degree of RA spans less sky the farther a field is from the celestial equator.
- It adds `centroid_error_px` (default 0.5 pixel) in quadrature, which is the square root of the sum of squares, to each axis, to allow for the error of finding a faint dot's centre (its centroid).
- The result is stored on each detection as `astrometric_error_ra_arcsec`, `astrometric_error_dec_arcsec` and `astrometric_error_correlation`, and `astrometric_error_arcsec` is the root mean square of the two axes.
- When a frame has too few matched stars to measure it, the detection carries no value and the test uses `astrometric_error_default_arcsec` (default 10 arcsec). The track records which case applied in `astrometric_error_assumed`, and the chain's error on each axis in `astrometric_error_ra_arcsec` and `astrometric_error_dec_arcsec`.

**Why it is several arcseconds.** This error is much larger than the 0.2 arcsecond scatter of a plate-solved stack (see the astrometry README), because a single frame is not plate solved. Its WCS is the mount's pointing corrected by one overall shift, not a fit to the frame's own stars (partly measured; see [MOV-06](../../../documentation/library_design/validation/asteroid_detection.md#mov-06)).

### Step 4. Track dots across frames and filter out noise

**What it does.** `detection.py`'s `MovingObjectDetector` links up detections of the same moving object across frames and runs a cascade of checks — is it detected in more than one frame, does it move in a straight line at a constant rate, is the rate within a plausible range — rejecting a candidate at whichever check it fails.

**What it records.** `models.moving_object.CascadeStage` records how far each candidate made it: a rejected candidate keeps the stage it was rejected at (for example `rejected_stationary_pixel`, a dead or hot pixel that never moved at all), and a surviving candidate reaches `rate_linearity_confirmed`.

The subsections below describe the parts of this step that decide the result.

#### RA wrap-around

Right Ascension wraps from 360 deg back to 0 deg, so a field or a mover can straddle RA = 0 (also written 0 h, because RA is often given in hours, 24 hours for the full circle). The detector sends every angular difference in RA, both the chaining search box and the offsets in the stationary and rate checks, through the shared helper `wrapped_ra_difference_deg` in `pipelines/shared/angles.py`. The pointing correction in `pipeline.py` does the same: it measures every reference star and detection as a wrapped offset from one centre RA before it builds its search tree (a k-d tree, a data structure for finding the nearest stars to a point quickly). The helper returns the short way around the circle, in the range (-180, 180] deg. New code in this package must use it instead of subtracting RA values.

#### Chaining runs inside one observing night

**What it does.** The detector groups detections by observing night (`observing_night_id` in `utilities/observing_night.py`; a night runs from local noon to local noon) and links dots within each night on its own. Within a night, a track may add a dot only if the dot lies within a match radius of the track's last dot.

**Setting.** That radius is `rate_max_arcsec_per_hour` (the fastest rate searched, default 300 arcsec per hour) times the elapsed hours, capped at `chain_match_radius_max_arcsec` (default 300 arcsec, 5 arcminutes; design estimate). Before it computes exact distances, the detector drops every dot outside an RA/Dec box slightly larger than the radius, so chaining stays fast on dense fields (see [MOV-07](../../../documentation/library_design/validation/asteroid_detection.md#mov-07)).

**Why.** Without the night split and the cap, the radius grows with the time between frames. For a gap of weeks it reaches a large part of a degree, and two unrelated stars seen on different nights can link into a slow false mover (partly measured; see [MOV-08](../../../documentation/library_design/validation/asteroid_detection.md#mov-08)).

**Limits.** Joining a mover that appears on more than one night is a separate step that does not exist yet. A real asteroid seen on two nights comes out as two separate candidates. The detector knows only each frame's time, so a "session" here is an observing night; it does not split by camera gain or offset the way `derive_target_sessions` does.

#### The straight-line test uses residuals in arcseconds

**What it measures, and why.** A real asteroid moves in a nearly straight line at a steady rate over one night, so its dots should sit close to a straight line in position against time. A residual is the distance of a dot from that fitted line. The test asks whether the residuals are no bigger than the measurement error would explain.

**The test.** The detector fits a straight line to the sky position against time, separately for RA and Dec. It then takes the root-mean-square (RMS: square each distance, average, take the root) distance of the dots from that line on each axis. The track passes when each axis's RMS is at most `residual_rms_max_multiple` (default 2; partly measured, see [MOV-03](../../../documentation/library_design/validation/asteroid_detection.md#mov-03)) times the position error of that axis, and when the rate is inside the allowed range.

The position error each axis is judged against comes from step 3.

**Why each axis is judged on its own.** Judging each axis on its own matters when they differ: with 1 arcsec east-west and 10 arcsec north-south, a 5 arcsec miss in right ascension is five times its error, but the combined error of 7 arcsec would have let it through (`test_each_axis_is_judged_against_its_own_error`).

#### The displacement condition (slow movers)

A third condition guards slow movers. The fitted line must carry the object at least `min_displacement_error_multiple` (default 3) times the position error between the first and the last picture. Without it, a star whose measured position jitters by its own error would pass the residual test, because a flat scatter fits a line with small residuals. Set the value to 0 to switch this condition off.

#### The direction check (back-and-forth chains)

A fourth condition rejects a chain that moves back and forth. The detector projects each dot onto the direction of the fitted motion and checks the projections in time order. A real mover only goes forward along that direction. For every pair of dots, the earlier one may lie ahead of the later one by at most the combined position error of the two (the square root of the sum of their squared errors).

A chain that fails this check, and meets every other condition, gets the stage `rejected_non_monotonic`. This catches a star whose measured centre flips between two points: its positions fit a line with small residuals, but they alternate along it. A chain that fails an earlier condition keeps that condition's stage, so the stage `rejected_non_monotonic` counts only chains that this check alone rejected.

#### What the track stores

R-squared (how much of the motion a straight line explains, from 0 to 1) is still computed and stored as `linear_fit_r_squared`. It does not decide anything: any chain with a large displacement scores near 1, even when its dots miss the line by many arcseconds.

A passing track also stores `residual_rms_right_ascension_arcsec`, `residual_rms_declination_arcsec`, `astrometric_error_arcsec` and `residual_limit_arcsec`. A rejected candidate keeps no track, so its residuals are not stored; the counts of tested and rejected chains go into the run metrics instead.

### Step 5. Check survivors against known asteroids

**What it does.** `ephemeris.py`'s `EphemerisCrossMatcher` asks an external ephemeris service (SkyBoT) which known minor planets are near a spot of the sky at a given time. For each surviving candidate it asks twice: at the time and position of the candidate's first detection, and at the time and position of its last detection.

**The match rule.** A known asteroid matches only if it lies within `ephemeris_cross_match_radius_arcsec` (default 10 arcsec) of the candidate at both moments.

**What it records.** A match reaches `ephemeris_matched` and stores the two separations in `first_detection_separation_arcsec` and `last_detection_separation_arcsec` (`angular_separation_arcsec` holds the larger one). An unmatched survivor stays at `rate_linearity_confirmed` — a real moving object, but not yet identified as a specific known body.

**Network use.** The matcher remembers each answer within a run, waits at least half a second between questions that go out over the network, and skips the second question when the first one fails.

**Limits.** Positions are the measured detection positions, not the fitted line, so the 10 arcsec radius sits close to the position error of a single frame (several arcseconds after the pointing correction, see step 3 and [MOV-06](../../../documentation/library_design/validation/asteroid_detection.md#mov-06)); a real match can miss at one end.

### Rotation period (not yet called)

`rotation_period.py` can also search a tracked object's brightness history for a repeating pattern, the way an asteroid's periodic brightness variation reveals its rotation. No pipeline step calls it yet, so a detection run does not report rotation periods.

## Where each piece lives

- `pipeline.py` is the coordinator: it ties together WCS estimation, detection, the pointing correction (step 3, which it implements itself), the rejection cascade, and ephemeris matching for one run.
- `runner.py` sits one level above `pipeline.py`. It runs the pipeline across a target's stored images and writes the results back onto the target's record.
- `detection.py`, `ephemeris.py` and `frame_wcs_composer.py` each implement one step above. `rotation_period.py` is the period search described after the steps; nothing calls it yet.
- `mpc_report.py` writes a confirmed track's detections as Minor Planet Center (MPC) 80-column observation lines and as ADES text. No pipeline step calls it yet, so a detection run does not produce MPC records; a caller must call its functions on a candidate. See "MPC report lines" below.

## The gate record

A gate is a pass/fail check on the run. The summary keeps one record per check in `gates`, built in `run_gates.py`. Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. A search that finds nothing says little unless it could have found something, so the first gate records whether it could. The tests in `test/test_run_gates.py` give each gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `search_frames` | Never | Fewer frames could be searched than a track needs (`min_frames_for_persistence`), so no mover could have been found |
| `pointing_metadata` | A frame lacks RA, Dec or image size in its header | No frame was considered |
| `ephemeris_cross_match` | The known-asteroid database (SkyBoT) could not be reached | No mover was confirmed, or the database was never asked |
| `unmatched_movers` | A mover moved in a straight line but matched no known asteroid (worth a manual look) | No mover was confirmed |
| `track_residuals` | A chain that reached the straight-line test had a detection with no measured position error, so the assumed `astrometric_error_default_arcsec` stood in for it | No chain reached the straight-line test |

**Query counts.** Without a count, a failed SkyBoT query would look the same as a field with no known asteroids. The pipeline counts the queries it makes and the ones that fail (`ephemerisQueriesAttempted`, `ephemerisQueriesFailed` in the metrics). Each confirmed mover costs up to two queries.

**The `track_residuals` value.** When `track_residuals` passes, its measured value is the largest ratio of RMS residual to position error among the accepted tracks, and its limit is `residual_rms_max_multiple`. The detail line also gives how many chains were tested, how many the residual test rejected, and how many were rejected for moving back along their direction. The run metrics carry the same counts as `residual_chains_tested`, `residual_chains_rejected` and `residual_chains_non_monotonic`.

**Not yet covered.** Two things are not yet covered: the false-mover rate of the rejection cascade and its recovery of injected movers (Gap 2), and whether the field is near the ecliptic (the band of sky around the plane of the Earth's orbit, where most asteroids appear) enough for a real asteroid to appear. A clean run on a field far from the ecliptic only confirms "found nothing" (see [MOV-04](../../../documentation/library_design/validation/asteroid_detection.md#mov-04)).

## MPC report lines

**The 80-column line.** `mpc_report.py` builds the classic 80-column MPC observation line: designation (the object's name or provisional number), UTC date to 1e-5 day, RA as `HH MM SS.ss`, Dec as `sDD MM SS.s`, an optional magnitude (brightness on the astronomer's scale, where larger is fainter) and band (the filter's colour range the magnitude was measured in), and the MPC observatory code.

**Observation time.** The date is the middle of the exposure: `DATE-OBS` (the moment the shutter opened, stored as the detection's `timestamp`) plus half of `EXPTIME` (stored as `exposure_seconds`). `mpc_lines_for_candidate` writes one line per detection and also returns how many lines used the exposure start because the frame had no `EXPTIME`; those times are early by half an exposure.

**Limits of the writer.**

- The designation is written as given. The writer does not convert a name like `2003 XY99` to the MPC's packed form.
- The detections carry an instrumental flux (the camera's raw measure of the object's light), not a calibrated magnitude, so `mpc_lines_for_candidate` leaves the magnitude and band columns blank.
- ADES is provided by `ades_psv_for_candidate` (below), in the pipe-separated layout only, not XML. It has not been run through the MPC's validator.
- Nothing in the pipeline submits a report. The functions are available for a caller to use.

### ADES report

**What it is.** `ades_psv_for_candidate` writes the Astrometry Data Exchange Standard (ADES) file the MPC prefers, because it carries an uncertainty for every observation, which the 80-column line cannot.

**Layout.** After the header blocks (`# version=2017`, observatory, submitter, observers, measurers and an optional telescope block) it writes one row per detection, oldest first:

- `trkSub` (the observer's own name for the track), `mode` (`CCD`), `stn` (the MPC code);
- `obsTime` (the middle of the exposure, UTC, to the millisecond);
- `ra` and `dec` (degrees);
- `rmsRA` and `rmsDec` (arcseconds on the sky, each frame's measured error on that axis), `rmsCorr` (the correlation of the two);
- `astCat` (the catalog the positions are tied to).

**The catalogue field.** `astCat` is left empty unless the caller says, because the writer cannot know. Since the Gaia refit of the plate solution (see the astrometry README), a frame's positions are tied to Gaia DR3 (`Gaia3`; the European Space Agency's Gaia star catalogue, third data release) when the run's `astrometry_flags` holds `wcs_refined_against_gaia`.

**Uncertainties.** A frame with no measured error gets the caller's assumed uncertainty and no correlation, and the result counts those rows (`assumed_error_rows`) so the caller can say so. The uncertainties are the scatter of the frame's field stars after the pointing correction plus the centroid error. They do not include the error of the pointing correction itself or of the reference stack's own solution, so they are lower limits on the error of an absolute position.

## Limits of this pipeline

- **No cross-night linking.** A mover seen on two or more nights is reported as separate candidates, one per night. Linking them needs an orbit-style fit over days, which this package does not do.
- **No search for fast movers, by decision.** The rate limit is 300 arcsec per hour (5 arcsec per minute, `rate_max_arcsec_per_hour`), and the detector keeps stars that are round (`roundness_range` of -0.6 to 0.6, on DAOStarFinder's roundness scale, which runs from about -1 to 1 with 0 for a perfectly round source). A faster object draws a streak in one exposure and is dropped. Streaks that fast are mostly satellites, and Starlink alone puts thousands in the sky, so a trail fit or a shift-and-add search (shifting frames along a guessed motion and adding them, so a faint mover builds up) would find mostly satellites. Recovery has been checked only for round sources up to 150 arcsec per hour, not for trailed ones (see [MOV-01](../../../documentation/library_design/validation/asteroid_detection.md#mov-01)). The decision is recorded in [`REVIEW_ACTION_PLAN.md`](../../../documentation/plans/REVIEW_ACTION_PLAN.md), section 1.5.
- **Short sequences only.** The chaining radius is capped at 5 arcminutes, so a fast mover (300 arcsec per hour) is followed across gaps of up to about one hour. Raise `chain_match_radius_max_arcsec` for longer gaps within a night, at the cost of more false links.
- **Slow movers need a long baseline.** The displacement condition means an object must travel about three times the position error between its first and last picture. With the assumed 10 arcsec error, that is 30 arcsec.
- **A reversal smaller than the position error passes.** A star that flips by less than the combined error of two dots is not caught by the direction check.
- **The measured position error reads low when it is large.** The estimate ignores stars farther than 15 arcsec from their nearest reference star, so a frame whose true scatter approaches that value is under-measured, and the test then allows less than intended.
- **Mid-exposure times need `EXPTIME`.** A frame without it gets the exposure start, and rates and reports are early by half an exposure.
- **The `track_residuals` limits are not yet validated on real fields.** They are checked on synthetic fields only (partly measured; see [MOV-03](../../../documentation/library_design/validation/asteroid_detection.md#mov-03)).

## What the detector can find

The detector's reach has been measured on synthetic fields and on two real fields. The results, and the library calls that produced them, are in the validation register:

- **Recovery of injected movers** by brightness and rate, including the faintest mover it finds (partly measured; see [MOV-01](../../../documentation/library_design/validation/asteroid_detection.md#mov-01)).
- **Slow movers.** The stationary test takes the largest distance of any detection from the track's mean position. For steady motion that is half the total motion. A slow mover whose half-motion stays under `pixel_match_tolerance_px` (default 1.5 pixel) or `sky_match_tolerance_arcsec` (default 3 arcsec) is rejected as a fixed source, at any brightness (partly measured; see [MOV-02](../../../documentation/library_design/validation/asteroid_detection.md#mov-02)).
- **False tracks in fields with no mover** (partly measured; see [MOV-03](../../../documentation/library_design/validation/asteroid_detection.md#mov-03)).
- **Confirmed movers on real fields** far from the ecliptic (partly measured; see [MOV-04](../../../documentation/library_design/validation/asteroid_detection.md#mov-04)).

## A note on scope

This pipeline finds and classifies moving objects; it does not stack, plate-solve, or catalog-identify the fixed stars in the same images — it depends on astrometry's per-frame star detection but does not duplicate astrometry's own plate-solving.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
