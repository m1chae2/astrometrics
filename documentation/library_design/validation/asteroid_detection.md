# Asteroid Detection Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

This file records how the tuned values of the moving-object (asteroid) detection pipeline were checked. Each entry names the library calls that were made, so you can repeat the check on your own frames and keep or change a value. It is written for a reader who wants to audit a number and for one who wants to tune the detector for another telescope or camera. Terms are defined in the [glossary](../Astrometrics_Library_Architecture.md#appendix-a-glossary), and the status labels are explained in [the register index](index.md).

A few terms recur below. A *mover* is a moving object, such as an asteroid. A *track* (or *chain*) is a set of detections, one per frame, that the detector links as one object. A track is *confirmed* when it passes every test and reaches the stage `rate_linearity_confirmed` or `ephemeris_matched`. *Peak brightness in sigma* is a mover's brightest pixel above the sky, divided by the standard deviation of the sky noise. The *position error* is how far a measured position on the sky may be from the true one, in arcseconds (arcsec, 1/3600 of a degree).

The pipeline finds sources in worker processes. Run the examples below as a saved script, with the pipeline calls under `if __name__ == "__main__":`, not pasted into an interactive prompt.

## Measured and partly measured values

### MOV-01

**Recovery of injected movers on synthetic fields**

- **Claim:** On synthetic fields, the detector confirms most movers whose peak is at least 8σ and whose rate is 20 to 150 arcsec per hour. It finds almost none below about 8σ, and none at 5 arcsec per hour at any brightness.
- **Settings it supports:**
  - Detection threshold: config key `[Processing.MovingObject] detection_threshold_sigma` (default 5.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Expected source width: config key `[Processing.MovingObject] detection_fwhm_px` (default 4.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Fewest frames in a track: config key `[Processing.MovingObject] min_frames_for_persistence` (default 3). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Allowed rates: config keys `[Processing.MovingObject] rate_min_arcsec_per_hour` (default 1.0) and `rate_max_arcsec_per_hour` (default 300.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
- **Status:** Partly measured. Synthetic fields only; the grid stops at 150 arcsec per hour and uses round sources.
- **Data you need:** None for the synthetic grid. To repeat it on your own sky, you need 8 or more light frames of one field taken within one night at a steady cadence, a plate-solved stack of the same field, and `RA`, `DEC` and `DATE-OBS` in every frame header.
- **Procedure:**
  1. Build a synthetic field with one injected mover and run the full pipeline on it. The harness writes 8 frames, 256 by 256 pixels, 300 s apart, at 1.8 arcsec/pixel, with 25 fixed stars and Gaussian sky noise. It then calls the same pipeline class a normal run uses. Both the harness and the pipeline class are internal (internal functions, not part of the public API; may change). <!-- impl: astrometricslib.scripts.validate_asteroid_detection.make_mover --> <!-- impl: astrometricslib.scripts.validate_asteroid_detection.write_synthetic_field --> <!-- impl: astrometricslib.scripts.validate_asteroid_detection.run_trial --> <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline -->
  2. Count the mover as recovered when a confirmed track lies within 3 pixels of the injected position in at least 3 frames. <!-- impl: astrometricslib.scripts.validate_asteroid_detection.candidate_follows_mover --> <!-- impl: astrometricslib.scripts.validate_asteroid_detection.MATCH_TOLERANCE_PX -->
  3. Repeat for 12 fields per cell of a grid of peak brightness (3, 5, 8, 12, 20σ) against rate (5, 20, 60, 150 arcsec per hour). <!-- impl: astrometricslib.scripts.validate_asteroid_detection.recovery_grid --> <!-- impl: astrometricslib.scripts.validate_asteroid_detection.format_recovery -->
  4. The confirmed tracks are sent to SkyBoT (the online list of known solar-system bodies). The script does not stub that query. Offline, the queries fail and the track stays at `rate_linearity_confirmed`, which still counts as confirmed. To run offline on purpose, give the cross-matcher a client that knows no bodies, as below. Save the example as a script and run it: the pipeline finds sources in worker processes, so the calls must sit under `if __name__ == "__main__":`. <!-- impl: astrometricslib.pipelines.asteroid_detection.ephemeris.EphemerisCrossMatcher -->

```python
from unittest import mock

import numpy as np

from astrometricslib import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection import pipeline as pipeline_module  # internal
from astrometricslib.scripts import validate_asteroid_detection as harness  # internal


class NoKnownBodies:
    """A SkyBoT stand-in that answers every question with an empty field."""

    def cone_search(self, *args, **kwargs):
        """Return no known bodies."""
        return None


real_matcher = pipeline_module.EphemerisCrossMatcher


def offline_matcher(config):
    """Build the cross-matcher with the stand-in client and no wait."""
    return real_matcher(config, skybot_client=NoKnownBodies(), min_seconds_between_queries=0.0)


if __name__ == "__main__":
    with mock.patch.object(pipeline_module, "EphemerisCrossMatcher", offline_matcher):
        # The whole grid, with the default settings.
        snrs, rates = (3.0, 5.0, 8.0, 12.0, 20.0), (5.0, 20.0, 60.0, 150.0)
        grid = harness.recovery_grid(snrs, rates, trials=12)
        print(harness.format_recovery(grid, snrs, rates))

        # One cell with a setting changed: 12 fields, 8σ, 60 arcsec/h.
        config = MovingObjectConfig(detection_threshold_sigma=4.0)
        generator = np.random.default_rng(0)
        hits = sum(
            harness.run_trial(
                harness.make_mover(8.0, 60.0, generator), int(generator.integers(1 << 30)), config
            ).recovered
            for _ in range(12)
        )
        print(f"recovered {hits} of 12")
```

  1. To match your own optics, set the harness's `PIXEL_SCALE_ARCSEC` and `SECONDS_BETWEEN_FRAMES` before the calls (for example `harness.PIXEL_SCALE_ARCSEC = 1.1`). Leave the frame count at 8: `write_synthetic_field` fixes it when the module loads. <!-- impl: astrometricslib.scripts.validate_asteroid_detection.PIXEL_SCALE_ARCSEC --> <!-- impl: astrometricslib.scripts.validate_asteroid_detection.SECONDS_BETWEEN_FRAMES -->
  2. To test on your own sky, add a Gaussian dot of known peak along a straight line to copies of your frames (open them with `memmap=False`). Then run `AsteroidDetectionPipeline(config).process(target_id, stack_path, frames)`, where `frames` is a list of `(path, unix_timestamp)` pairs, and check whether a confirmed candidate's `frame_detections` follow the line. <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline.process --> <!-- impl: astrometricslib.AsteroidDetectionCandidate -->

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The completeness (recall) in each cell, from 0% to 100%. A good detector reaches high completeness at the brightness and rates you care about. The brightness where the completeness rises from near 0 to most fields is the practical detection limit; compare it with `detection_threshold_sigma`.
- **Our result:** Synthetic fields, 12 per cell, run on 2026-10-10. Re-run on 2026-10-11, after the direction check, the per-axis residual test and the Gaia refit were added: every cell came out the same (the fields are seeded).

  **Table 1.** Completeness (recall) for injected movers, by peak brightness and rate.

  | Peak brightness | 5 arcsec/h | 20 arcsec/h | 60 arcsec/h | 150 arcsec/h |
  | --- | --- | --- | --- | --- |
  | 3σ | 0% | 0% | 0% | 0% |
  | 5σ | 0% | 0% | 0% | 8% |
  | 8σ | 0% | 83% | 83% | 92% |
  | 12σ | 0% | 67% | 83% | 92% |
  | 20σ | 0% | 83% | 92% | 100% |

  At 8σ or brighter and 20 to 150 arcsec per hour, recovery ran from 67% to 100%. A mover was not found below about 8σ. That is well above the 5σ detection threshold of a single frame, and the reason for the gap has not been measured. The 5 arcsec per hour column is explained in [MOV-02](#mov-02).
- **Verdict:** Not judged.
- **Runs:** The earlier run is superseded by the later one; the numbers did not change.

  | Date | Code version | Verdict | Record |
  | --- | --- | --- | --- |
  | 2026-10-10 | Not recorded. | Not judged. | None kept; Table 1 above is the record. |
  | 2026-10-11 | Not recorded. Run after the direction check, the per-axis residual test and the Gaia refit were added. | Not judged. | None kept; every cell matched the first run (the fields are seeded). |

- **Limits:** The synthetic frames share one pointing, so they test detection and chaining, not the position error of real frames. Twelve fields per cell gives coarse completeness: one field is 8 percentage points. The grid stops at 150 arcsec per hour and injects round sources only, so it says nothing about trailed movers. Star density is fixed at 25 stars per frame.
- **To tune for your setup:** To find fainter movers, lower `detection_threshold_sigma` and re-run the grid together with the mover-free fields of [MOV-03](#mov-03), since a lower threshold also admits more noise. Set `detection_fwhm_px` to your typical star width in pixels. Set the harness pixel scale and cadence to yours before you judge the rate columns.
- **Regenerate with:** `astrometricslib/scripts/validate_asteroid_detection.py` (`python -m astrometricslib.scripts.validate_asteroid_detection --synthetic`, with `--trials 12` as the default number of fields per cell).
- **Guarded by tests:** `astrometricslib/scripts/test/test_validate_asteroid_detection.py` tests the harness.
- **Raw record:** None kept; Table 1 above is the record.

### MOV-02

**Slow movers are rejected as stationary**

- **Claim:** A mover at 5 arcsec per hour is never confirmed in an 8-frame, 35-minute sequence, at any brightness. Its dots stay inside the stationary tolerances, so the detector rejects it as a fixed source.
- **Settings it supports:** The evidence shows a consequence of these values, not that they are the right ones.
  - Pixel tolerance for a hot pixel: config key `[Processing.MovingObject] pixel_match_tolerance_px` (default 1.5). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Sky tolerance for a fixed star: config key `[Processing.MovingObject] sky_match_tolerance_arcsec` (default 3.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - The stationary test itself (internal function, not part of the public API; may change). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._evaluate_reference_frame_test -->
- **Status:** Partly measured. Synthetic fields only; the arithmetic below explains the result exactly.
- **Data you need:** None for the synthetic case. On real data, a sequence with a known slow mover of known rate.
- **Procedure:**
  1. Run the 5 arcsec per hour column of the [MOV-01](#mov-01) grid.
  2. Work out the expected spread. The stationary test takes the largest distance of any dot from the track's mean position. For steady motion that is half the total motion. Total motion is rate times the time from the first to the last frame.
  3. Compare the half-motion with both tolerances, in pixels and in arcseconds.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Half the total motion, in pixels and in arcseconds. If it is below `pixel_match_tolerance_px` or below `sky_match_tolerance_arcsec`, the mover is rejected as `rejected_stationary_pixel` or `rejected_stationary_sky`.
- **Our result:** Synthetic fields (1.8 arcsec/pixel, 8 frames 300 s apart), 2026-10-10 and 2026-10-11: 0% recovery at 5 arcsec per hour for every brightness (Table 1 of MOV-01). In the 35 minutes from the first to the last frame, such a mover travels 2.9 arcsec, or 1.6 pixels. Half of that is about 0.8 pixel and 1.5 arcsec, below both the 1.5 pixel and the 3 arcsec tolerance.
- **Verdict:** Not judged.
- **Limits:** Only one cadence and one pixel scale were tested. A longer sequence in the same night moves a slow mover farther and can lift it over the tolerances.
- **To tune for your setup:** For slow movers, observe a longer span within one night, so that half the total motion clears both tolerances. Lowering the tolerances also helps, but the [MOV-03](#mov-03) mover-free run should be repeated, because stars whose centers jitter can then pass as slow movers.
- **Regenerate with:** `astrometricslib/scripts/validate_asteroid_detection.py` (`--synthetic`), as for MOV-01.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### MOV-03

**False tracks in synthetic fields with no mover**

- **Claim:** In 40 synthetic fields with no mover, the current detector confirms no track. Without the direction check, the residual test alone let one false track through: a star whose measured center flipped between two points.
- **Settings it supports:**
  - Residual limit: config key `[Processing.MovingObject] residual_rms_max_multiple` (default 2.0). Relation: confirmed by this entry. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Displacement limit: config key `[Processing.MovingObject] min_displacement_error_multiple` (default 3.0). Relation: confirmed by this entry. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - The direction check, which has no setting of its own (internal function, not part of the public API; may change). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection._reverses_direction -->
- **Status:** Partly measured. Synthetic fields only; the limits of the residual test are not validated on real fields.
- **Data you need:** None for the synthetic case. On real data, frames of a field far from the ecliptic (the band of sky around the plane of the Earth's orbit, where most asteroids appear), where a confirmed mover is almost certainly false. See [MOV-04](#mov-04).
- **Procedure:**
  1. Run the pipeline on mover-free synthetic fields and count the confirmed tracks in each. <!-- impl: astrometricslib.scripts.validate_asteroid_detection.false_track_counts --> <!-- impl: astrometricslib.scripts.validate_asteroid_detection.run_trial -->
  2. For any confirmed track, read its `track` record: `residual_rms_right_ascension_arcsec`, `residual_rms_declination_arcsec`, `residual_limit_arcsec` and `linear_fit_r_squared`. Plot its `frame_detections` to see what it is.
  3. Read the residual-test counts from the pipeline's run metrics (`residual_chains_tested`, `residual_chains_rejected`, `residual_chains_non_monotonic`). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline -->

```python
from astrometricslib.scripts import validate_asteroid_detection as harness  # internal

counts = harness.false_track_counts(40)
print(f"{sum(counts)} confirmed tracks; {sum(1 for c in counts if c)} fields with at least one")
```

- **Acceptance criterion:** 0 confirmed tracks in fields that hold no mover. Every confirmed track there is false, so the target is 0.
- **What to look at:** The number of confirmed tracks in fields that hold no mover. Every one is false, so the target is 0. Also the stage counts: a rise in `rejected_non_monotonic` shows the direction check doing the work.
- **Our result:** 40 synthetic fields with no mover. Current run, 2026-10-11, after the direction check was added: 0 confirmed tracks in the 40 fields. The flipping star is reported as `rejected_non_monotonic`. The first run, 2026-10-10, had the residual test but not the direction check, and confirmed one track. It was a star whose measured center flipped between two positions 2.8 pixels apart. Its positions fit a line to within 1.8 arcsec, the limit here (twice the 0.9 arcsec position error of these frames). Its R-squared (the share of the motion a straight line explains, from 0 to 1) was nonetheless only 0.40. The R-squared test that the residual test replaced rejected that track and confirmed none in the same 40 fields.
- **Verdict:** Pass. The current detector confirmed 0 tracks in 40 fields, against a target of 0.
- **Runs:** The current run is the latest row.

  | Date | Code version | Verdict | Record |
  | --- | --- | --- | --- |
  | 2026-10-10 | Not recorded. Had the residual test, before the direction check. | Fail | None kept. One confirmed track: a star whose measured center flipped between two points. |
  | 2026-10-11 | Not recorded. Run after the direction check was added. | Pass | None kept. 0 confirmed tracks in 40 fields. |

- **Limits:** The synthetic fields share one pointing and have almost no position error (0.9 arcsec), so the residual and displacement limits were not stressed by real per-frame errors of several arcseconds. Forty fields can only show that the false alarms per field are few, not how few.
- **To tune for your setup:** If real fields give false tracks that pass on residuals, lower `residual_rms_max_multiple` or raise `min_displacement_error_multiple`, then re-run both this check and the [MOV-01](#mov-01) grid, since both changes also cost real movers.
- **Regenerate with:** `astrometricslib/scripts/validate_asteroid_detection.py` (`python -m astrometricslib.scripts.validate_asteroid_detection --synthetic`, with `--null-trials 40` as the default).
- **Guarded by tests:** `astrometricslib/pipelines/asteroid_detection/test/test_residual_criterion.py` (`test_a_star_whose_centre_flips_between_two_points_is_rejected_as_non_monotonic`) covers the flipping-star case.
- **Raw record:** None kept.

### MOV-04

**Confirmed movers on real fields far from the ecliptic**

- **Claim:** On real frames of fields far from the ecliptic, the detector confirmed at most one mover per field, and none matched a known asteroid. Such a mover is almost certainly false (a satellite, a hot pixel or an artifact). The number of false alarms per field on real fields is not measured.
- **Settings it supports:**
  - All the track tests together, chiefly config keys `[Processing.MovingObject] min_frames_for_persistence` (default 3) and `residual_rms_max_multiple` (default 2.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
- **Status:** Partly measured. Two fields; the counts predate the residual test and the per-night chaining and have not been repeated since.
- **Data you need:** A sequence of light frames of a field far from the ecliptic (tens of degrees), taken in one night, with a plate-solved stack, `RA`, `DEC` and `DATE-OBS` in every header, and network access to SkyBoT.
- **Procedure:**
  1. Run the asteroid stage through the public API, then read the counts and the gate record (pass/fail checks on the run). <!-- impl: astrometricslib.Astrometrics --> <!-- impl: astrometricslib.api.processing.ProcessingPipelines.process_target -->

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
target = astrometrics.targets.get("Your target")
astrometrics.processing.process_target(target, stages=["asteroids"])

summary = target.asteroid_detection.quality_summary
metrics = summary.asteroid_detection_metrics
print("frames searched:", metrics.frames_with_wcs_estimate)
print("confirmed movers:", metrics.candidates_rate_linearity_confirmed)
print("matched to known bodies:", metrics.candidates_ephemeris_matched)
for gate in summary.gates:
    print(gate.name, gate.status, gate.measured_value, gate.detail)
```

  1. Or run the read-only counter, which does not change the target record. <!-- impl: astrometricslib.scripts.validate_asteroid_detection.real_field_counts -->

- **Acceptance criterion:** 0 confirmed movers that match no known body (0 is the goal).
- **What to look at:** Confirmed movers that match no known body (`candidates_rate_linearity_confirmed` minus `candidates_ephemeris_matched`, and the `unmatched_movers` gate). Far from the ecliptic each one is almost certainly false, so 0 is the goal. Check `ephemeris_queries_failed` first: a failed query looks the same as a field with no known bodies.
- **Our result:** NGC 2403 (DS-NGC2403, 35 frames): one confirmed mover that matched no known asteroid. M 101 (40 frames): none. Date not recorded; both runs predate the residual test and the per-night chaining. The fields were chosen because they lie far from the ecliptic; the validation scripts give NGC 2403 and M 81 as 43 to 52° from it.
- **Verdict:** Fail. NGC 2403 gave one confirmed mover that matched no known asteroid, against a goal of 0; M 101 gave none. Both runs predate the residual test and the per-night chaining.
- **Limits:** Two fields, one night each, with older track tests. A clean run on a field far from the ecliptic only confirms "found nothing". It cannot show that a real asteroid would be matched. No field near the ecliptic with a known asteroid has been run.
- **To tune for your setup:** Run your own far-from-ecliptic fields first. If they give unmatched movers, inspect them and tighten the track tests as in [MOV-03](#mov-03). Then run a field near the ecliptic with a known asteroid, to check that the tightened tests still keep it.
- **Regenerate with:** `astrometricslib/scripts/validate_asteroid_detection.py` (`python -m astrometricslib.scripts.validate_asteroid_detection --real "NGC 2403" "M 81"`, with `--maximum-frames N` to use only the first N frames). `astrometricslib/scripts/run_empirical_validation.py` (`python -m astrometricslib.scripts.run_empirical_validation --camera "ZWO ASI 533MM Pro"`) runs the stage on NGC 2403, M 81, M 1, M 45, M 67, NGC 2903, M 16 and NGC 1893 and saves the results to the catalog. No evidence file.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### MOV-05

**Pointing correction by an offset vote**

- **Claim:** Without a per-frame pointing correction, the mount's pointing error makes fixed stars look like movers. A vote over every star-to-detection offset finds the shared shift where a nearest-star match does not.
- **Settings it supports:**
  - Pairing radius: code constant `_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC` = 300.0 in `astrometricslib/pipelines/asteroid_detection/pipeline.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC -->
  - Vote bin width: code constant `_POINTING_CORRECTION_VOTE_BIN_ARCSEC` = 5.0 in `astrometricslib/pipelines/asteroid_detection/pipeline.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._POINTING_CORRECTION_VOTE_BIN_ARCSEC -->
- **Status:** Partly measured. One target, NGC 2403, for the vote; not tried on a range of mount pointing errors.
- **Data you need:** A sequence of light frames from a mount that is not synced by plate solving, with `RA` and `DEC` in each header, and a plate-solved stack of the same field.
- **Procedure:**
  1. Build each frame's WCS (World Coordinate System, the map from pixel to sky position) from the mount's pointing and the stack's rotation and scale (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.asteroid_detection.frame_wcs_composer.estimate_frame_wcs_from_mount_pointing -->
  2. Compare each frame's detections with the stack's stars to see the raw pointing error, in arcseconds.
  3. Run the full pipeline and read the per-detection `astrometric_error_ra_arcsec` and `astrometric_error_dec_arcsec` of the returned candidates. `AsteroidDetectionPipeline.process` returns every candidate, rejected ones included. A detection with no value comes from a frame where the vote failed. The vote itself is internal (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._estimate_bulk_pointing_correction_deg --> <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline.process -->
  4. To try other values, edit the two constants and repeat step 3.
- **Acceptance criterion:** A working correction leaves a position error of a few arcseconds and few unmatched movers.
- **What to look at:** The raw per-frame pointing error, the median spacing of the field's stars, and the per-frame position error after the correction, all in arcseconds. Also the number of confirmed movers that match no known body. A working correction leaves a position error of a few arcseconds and few unmatched movers.
- **Our result:** Before the correction was added, real NGC 2403 (DS-NGC2403, 36 frames) and M 81 (DS-M81, 257 frames) data gave 11,990 and 19,068 track candidates and 187 and 97 confirmed movers, with no SkyBoT matches. The per-frame mount offsets measured against NGC 2403's own stars were 10 to 40 arcsec. A plain nearest-star match left a residual scatter of about 13 arcsec, because NGC 2403's stars are a median 48 arcsec apart, close to the size of the pointing error, so many detections paired with the wrong star. The vote with 5 arcsec bins replaced it. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** One target set the bin width. The radius was chosen to contain a mount error of several arcminutes; mount errors near that size have not been tested.
- **To tune for your setup:** If your mount is off by more than a few arcminutes, raise the pairing radius. If your field is much denser than NGC 2403, narrow the bin so that chance pairs spread out, but keep it well above your centroid noise. Re-run step 3 after each change.
- **Regenerate with:** No script; measured by hand, date not recorded. The source is the comment beside the constants in `pipeline.py`.
- **Guarded by tests:** Unit tests check that the vote recovers a known offset: `astrometricslib/pipelines/asteroid_detection/test/test_pointing_correction_ra_wrap.py`.
- **Raw record:** None kept.

### MOV-06

**Per-frame position error after the pointing correction**

- **Claim:** After the pointing correction, the position error of one frame is several arcseconds, much larger than the error of a plate-solved stack. The match radius for known bodies and the assumed error are of the same size.
- **Settings it supports:** It bears on two values that remain design estimates (see [Design estimates](#design-estimates)), because no record of the error range was kept:
  - Known-body match radius: config key `[Processing.MovingObject] ephemeris_cross_match_radius_arcsec` (default 10.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Assumed error when none is measured: config key `[Processing.MovingObject] astrometric_error_default_arcsec` (default 10.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
- **Status:** Partly measured. The range is quoted in the method documents, but no run, script or record behind it was found.
- **Data you need:** As for [MOV-05](#mov-05).
- **Procedure:**
  1. Run the pipeline on your frames (internal class, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline.process -->
  2. Collect `astrometric_error_arcsec` from every detection of every returned candidate, one value per frame.

```python
from astrometricslib import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.pipeline import AsteroidDetectionPipeline  # internal

frames = [("/data/frame_001.fits", 1700000000.0), ("/data/frame_002.fits", 1700000300.0)]
candidates = AsteroidDetectionPipeline(MovingObjectConfig()).process("my-target", "/data/stack.fits", frames)
per_frame = {}
for candidate in candidates:
    for detection in candidate.frame_detections:
        if detection.astrometric_error_arcsec is not None:
            per_frame[detection.frame_path] = detection.astrometric_error_arcsec
print(min(per_frame.values()), max(per_frame.values()))
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The spread of per-frame position errors, in arcseconds. Compare it with `ephemeris_cross_match_radius_arcsec`: when the error approaches the radius, a real match can miss at one end of the track. Errors close to 15 arcsec read low, because the estimate drops stars farther than that from their nearest reference star (`_POSITION_ERROR_MATCH_RADIUS_ARCSEC`). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._POSITION_ERROR_MATCH_RADIUS_ARCSEC -->
- **Our result:** About 5 to 13 arcsec per frame after the pointing correction. Data set and date not recorded.
- **Verdict:** Not judged.
- **Limits:** No record of the run. The estimate leaves out the error of the correction itself and of the stack's own solution, so it is a lower limit on the error of an absolute position.
- **To tune for your setup:** Set `ephemeris_cross_match_radius_arcsec` comfortably above your largest per-frame error, and `astrometric_error_default_arcsec` near your typical one.
- **Regenerate with:** No script and no record found.
- **Guarded by tests:** The per-frame estimate is unit-tested in `astrometricslib/pipelines/asteroid_detection/test/test_position_error_estimate.py`.
- **Raw record:** None kept.

### MOV-07

**Bounding-box pre-filter speeds up chaining**

- **Claim:** Before the detector computes exact angular separations, it drops every pair outside an RA/Dec box around the match radius. On a dense field this removes almost all pairs and makes chaining take seconds, with the same tracks as an exhaustive search.
- **Settings it supports:** No tuned value. The box is the match radius times 1.2, widened in RA by 1/cos(Dec), inside the chaining step (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._chain_detections_by_persistence -->
- **Status:** Partly measured. One field, timed once.
- **Data you need:** A dense field with tens of thousands of detections across tens of frames.
- **Procedure:**
  1. Run the pipeline once and gather every detection from the returned candidates (every detection belongs to some candidate, single-frame ones included). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline.process -->
  2. Time the chaining and tests on those detections alone; `candidates` below is the list from step 1, as in [MOV-06](#mov-06) (internal class, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector.detect_candidates -->

```python
import time

from astrometricslib import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.detection import MovingObjectDetector  # internal

detections = [d for candidate in candidates for d in candidate.frame_detections]
start = time.perf_counter()
MovingObjectDetector(MovingObjectConfig()).detect_candidates("my-target", detections)
print(f"{len(detections)} detections chained in {time.perf_counter() - start:.1f} s")
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Wall-clock time of the chaining step, in seconds, and whether the tracks match an exhaustive search.
- **Our result:** M 81 (DS-M81), about 150,000 detected sources across 46 frames: the box removed 99.9% of pairs, and the step went from over 20 minutes to seconds, with the same tracks. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** One field, one machine. The search still looks at every pair; it only does so in one vectorized NumPy comparison.
- **To tune for your setup:** Nothing to tune. If chaining is slow, lower `chain_match_radius_max_arcsec` to shrink the box.
- **Regenerate with:** No script; measured by hand, date not recorded.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### MOV-08

**Chaining within one observing night**

- **Claim:** When chaining spans nights, the match radius grows with the gap, and two unrelated stars seen weeks apart can link into a slow false mover. Chaining within one night, with a capped radius, prevents it.
- **Settings it supports:**
  - Radius cap: config key `[Processing.MovingObject] chain_match_radius_max_arcsec` (default 300.0). Relation: sanity check only. <!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
  - Night split: the detector groups detections by observing night, local noon to local noon (internal function, not part of the public API; may change). Relation: derived from this entry. <!-- impl: astrometricslib.utilities.observing_night.observing_night_id -->
- **Status:** Partly measured. A synthetic test case, not a real multi-night data set.
- **Data you need:** None for the synthetic case. On real data, frames of one field from two nights weeks apart.
- **Procedure:**
  1. Build detections of two fixed stars half a degree apart, one on each of two nights a month apart, and run the detector on them (internal class, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector.detect_candidates -->
  2. Count confirmed movers. Then build a real mover inside one night and check that its rate comes out right.
- **Acceptance criterion:** 0 confirmed movers from the two-star case, and the fitted rate of the one-night mover matches its true rate.
- **What to look at:** Confirmed movers from the two-star case (should be 0), and the fitted rate of the one-night mover against its true rate, in arcsec per hour.
- **Our result:** Without the night split and the cap, the radius grew to 1° for a gap of weeks, and the two stars linked into a false mover at 2.5 arcsec per hour. With per-night chaining, no mover forms, and a real mover inside one night is found at its true rate.
- **Verdict:** Not judged.
- **Limits:** A synthetic case. Real movers seen on two nights now come out as two separate candidates; nothing joins them.
- **To tune for your setup:** Raise `chain_match_radius_max_arcsec` for long gaps within one night, at the cost of more false links. Re-run [MOV-03](#mov-03) afterwards.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/asteroid_detection/test/test_session_chaining.py` (`test_two_stars_half_a_degree_apart_a_month_apart_make_no_mover`, `test_a_real_mover_is_found_on_one_night_with_its_true_rate`, `test_the_match_radius_stops_growing_at_the_cap_inside_one_night`).
- **Raw record:** None kept.

## Design estimates

These values have not been measured. Each is a reasoned choice.

**Table 2.** Asteroid detection design estimates.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| MOV-D01 | Fewest frames in a track | 3 | config key `[Processing.MovingObject] min_frames_for_persistence` | More false tracks (too low) or missed movers (too high). | Re-run the [MOV-01](#mov-01) grid and the [MOV-03](#mov-03) mover-free fields at 3, 4 and 5 with `run_trial(..., config=MovingObjectConfig(min_frames_for_persistence=N))`. |
| MOV-D02 | Stationary tolerances | 1.5 px; 3 arcsec | config keys `pixel_match_tolerance_px`, `sky_match_tolerance_arcsec` | Slow movers rejected as stationary, or hot pixels kept. | Run your real frames through `AsteroidDetectionPipeline.process` and plot the pixel and sky spreads of the `rejected_stationary_*` candidates; see also [MOV-02](#mov-02). |
| MOV-D03 | Allowed rate range | 1 to 300 arcsec/h | config keys `rate_min_arcsec_per_hour`, `rate_max_arcsec_per_hour` | Movers outside the range are missed. | Inject movers at the edges of the range with `make_mover` and count recoveries; the upper limit is a policy choice (faster objects trail and are mostly satellites). |
| MOV-D04 | Chaining radius cap | 300 arcsec | config key `chain_match_radius_max_arcsec` | False links across a gap, or true links missed. | Run real sequences with gaps inside one night and count false links in `run_trial` mover-free fields as the cap rises. |
| MOV-D05 | Assumed position error | 10 arcsec | config key `astrometric_error_default_arcsec` | Track tests too strict or too loose. | Use the per-frame errors from [MOV-06](#mov-06); set this near their typical value. |
| MOV-D06 | Centroid error | 0.5 px | config key `centroid_error_px` | Residual limits scaled wrongly. | Measure the scatter of a faint star's center across frames after the pointing correction. |
| MOV-D07 | Known-body match radius | 10 arcsec | config key `ephemeris_cross_match_radius_arcsec` | Real matches missed, or wrong bodies matched. | Run a field near the ecliptic with a known asteroid and read `first_detection_separation_arcsec` and `last_detection_separation_arcsec` from the match. |
| MOV-D08 | Fewest votes for the pointing shift | 5 | code constant `_POINTING_CORRECTION_MIN_VOTES` in `pipeline.py` | A wrong pointing correction. | Count frames whose detections carry no measured position error after `AsteroidDetectionPipeline.process`, and check them by eye. |
| MOV-D09 | Match radius for the position-error estimate | 15 arcsec (three vote bins) | code constant `_POSITION_ERROR_MATCH_RADIUS_ARCSEC` in `pipeline.py` | Position error read too low. | Compare the estimate with the scatter of hand-matched stars on frames with known large errors. |
| MOV-D10 | Source shape limits | roundness -0.6 to 0.6; sharpness 0.2 to 1.0 | `SourceDetector` in `astrometricslib/pipelines/astrometry/pre_processing/source_detection.py` | Trailed movers dropped. | Inject trailed movers and count how many the shape limits drop. |
