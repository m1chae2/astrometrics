# Stacking Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

This file lists the evidence behind the tuned numbers of the stacking pipeline: the limits, thresholds and defaults that the method documents state without proof. Each entry names the setting, the data you need, the library calls that produce the number, what we measured, and how to tune the setting on your own equipment. It is written for two readers: a learner who wants to see how a number was found, and a reviewer who wants to repeat the check. Terms are defined in the [glossary](../../Glossary.md), and the status labels (Measured, Partly measured, Design estimate) are explained in [index.md](index.md).

Every Python example assumes the library is installed and configured with your own frame library. `astro = Astrometrics()` reads your configuration, and target names such as `"M 27"` stand for targets in your library. Functions marked "internal" are not part of the public API: they are named so you can repeat a measurement exactly, and they may change. FITS files are opened with `memmap=False` so that no file handle stays open.

## Measured and partly measured values

### STK-01

**Which frames the quarantine moves (clouds and trailed stars)**

- **Claim:** Within one session, half the median star count separates clouded frames from clear ones, and a roundness limit of 0.80, applied only when a frame is also 0.15 below the session median, separates trailed frames from round ones. A batch of eight frames is enough for a stable median.
- **Settings it supports:**
  - code constant `CLOUD_STAR_FRACTION` = 0.5 in `pipelines/stacking/pre_processing/frame_quarantine.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.CLOUD_STAR_FRACTION -->
  - code constant `TRAILED_ROUNDNESS_LIMIT` = 0.80 in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.TRAILED_ROUNDNESS_LIMIT -->
  - code constant `ROUNDNESS_DROP_BELOW_MEDIAN` = 0.15 in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.ROUNDNESS_DROP_BELOW_MEDIAN -->
  - code constant `MINIMUM_FRAMES_TO_JUDGE` = 8 in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MINIMUM_FRAMES_TO_JUDGE -->
  - code constant `CLOUD_SKY_MULTIPLE` = 1.15 in the same file (it changes only the wording of the reason). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.CLOUD_SKY_MULTIPLE -->
  - on/off: config key `[Processing.Siril] quarantine_bad_frames_enabled` (default `true`). Relation: sanity check only.
- **Status:** Partly measured. The limits sit between clean and bad frames of one target and a few M 27 sessions, with only two clouded and two trailed frames as known bad cases.
- **Data you need:** At least one session of 20 or more monochrome light frames of one field (same camera, filter and exposure), in which you can tell by eye which frames had cloud or poor tracking. A session that clouds over part way through, or one with a known mount drift, is ideal.
- **Procedure:**
  1. Measure every frame of the session and look at the star count, roundness and sky level of each.
  2. Ask what the quarantine would move, without moving anything.
  3. Compare the frames it would move with the frames you judged bad by eye.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  diagnostics = astro.quality
  # Step 1: per-frame star count, roundness and sky level for one session.
  check = diagnostics.frame_quality(
      "M 27", kind="raw_check", filter_name="L", since="2026-09-24T18:00", until="2026-09-25T06:00", limit=300
  )
  for row in check.frames:
      print(row.get("path"), row.get("star_count"), row.get("roundness"), row.get("sky_median_adu"))
  # Step 2: what the quarantine would move, and why. Nothing is moved.
  preview = diagnostics.frame_quality("M 27", kind="quarantine_preview", limit=300)
  for frame in preview.would_move:
      print(frame.path, frame.reason)
  print(preview.notes)
  ```

  `QualityDiagnostics.frame_quality` <!-- impl: astrometricslib.api.quality.QualityDiagnostics.frame_quality --> runs the same measurement (`measure_raw_frame`, internal function, not part of the public API; may change) <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.measure_raw_frame --> and the same judgment (`judge_batch`, internal function, not part of the public API; may change) <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.judge_batch --> as the stacker.
- **Acceptance criterion:** A good limit moves every frame you judged bad and none you judged good. For the batch-size check, the median star count of every run of eight consecutive clean frames stays well inside the factor of 2 that the star limit allows.
- **What to look at:** Each frame's star count divided by the session median (no units; clear frames sit near 1), its median roundness (narrow width over wide width, 1.0 for a circle), and its sky level divided by the session median. A good limit moves every frame you judged bad and none you judged good. For the batch-size check, take the median star count of every run of eight consecutive clean frames and compare it with the median of the whole session: the spread should stay well inside the factor of 2 that the star limit allows.
- **Our result:** On a 56-frame monochrome target of 2026-10-02 (target name not recorded):

  | Measurement | Clean frames | Bad frames |
  | --- | --- | --- |
  | Detected stars (median 3,348) | 3,076 to 3,651 (0.92 to 1.09 of the median) | 1,245 (0.37) and 203 (0.06), shot as cloud thickened |
  | Sky level, times the median | at most 1.09, with the moon rising | 2.25 and 3.52 (the two clouded frames) |
  | Median star roundness | 0.87 to 0.99 | 0.44 and 0.65 (mount drift) |

  The mildest cloud frame held 2,966 stars (0.89) with the sky 1.17 times normal and stays in the stack. A frame with one 4 arcsec guiding spike (roundness 0.90) stays. On the M 27 session of 2026-09-24 (median roundness 0.90), the fixed 0.80 limit alone moved nine of 40 frames. The 0.15 drop (a cut-off of 0.75 there) moves the five clearly trailed frames (0.47, 0.49, 0.55, 0.71, 0.74) and keeps four mildly elongated ones. Batch size: on the 2026-10-02 target the median of any eight consecutive clean frames stayed within 3.9% of the whole session's; on four older M 27 sessions (18 to 60 frames) it stayed within 5.8% to 18.3%.
- **Verdict:** Not judged.
- **Limits:** Two clouded and two trailed frames make a small set of known bad cases. The sessions are all from the ZWO ASI533MM Pro (a monochrome camera). The 1.15 sky multiple only changes the text of the reason.
- **To tune for your setup:** If good frames are moved, lower `TRAILED_ROUNDNESS_LIMIT` or raise `ROUNDNESS_DROP_BELOW_MEDIAN`; if clouded frames stay, raise `CLOUD_STAR_FRACTION`. Re-run step 2 after each change. If your sessions are short, check the eight-frame spread before lowering `MINIMUM_FRAMES_TO_JUDGE`.
- **Regenerate with:** No script; measured by hand with the calls above (dates in the result).
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_frame_quarantine.py` and `test_frame_quarantine_gate.py` check the rules on made-up batches.
- **Raw record:** None kept.

### STK-02

**A wider roundness allowance for color cameras**

- **Claim:** A color (Bayer) camera with small, coarsely sampled stars measures roundness less steadily, so it needs a drop of 0.20 below the session median instead of 0.15. At 0.20 every clearly bad frame is still caught and no good frame is.
- **Settings it supports:**
  - code constant `COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN` = 0.20 in `pipelines/stacking/pre_processing/frame_quarantine.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN -->
- **Status:** Measured. 667 frames of eight Nikon D5300 targets, with four monochrome targets as controls.
- **Data you need:** Several sessions from a color camera whose FITS headers carry a `BAYERPAT` keyword, with stars 2 to 3 pixels wide, and a list of the frames you judge bad by eye (for example satellites, dawn, cloud).
- **Procedure:**
  1. Run the quarantine preview of STK-01 on each target.
  2. For each frame it would move, note how far its roundness sits below its session median.
  3. Check that every frame you judged bad sits at least 0.20 below its median, and look at the thumbnails of the frames between 0.15 and 0.20 below.
- **Acceptance criterion:** The allowance is the largest one that still catches every bad frame.
- **What to look at:** The drop below the session median for each moved frame (no units). The right allowance is the largest one that still catches every bad frame.
- **Our result:** On 667 frames of eight Nikon D5300 targets (checked again on 2026-10-13), session medians were 0.73 to 0.97, against 0.97 to 0.99 on the mono ZWO ASI533MM Pro. The 37 frames moved on 2026-10-12 with 0.15 included 14 that sat 0.15 to 0.20 below their median with nothing visibly wrong. The 13 clearly bad frames (the dawn run of NGC 6888 frames 159 to 168, NGC 6888 008, M 106 035 and 018, NGC 2024 004) sat 0.20 to 0.45 below their medians; 0.25 would miss two of them. No frame kept before is caught now, and the mono controls (M 13, M 57, NGC 7331, M 81) decide as before. Measuring on 2 by 2 summed pixels changed roundness by a median of 0.004 (at most 0.07) on 1,070 frames, so the color filter pattern is not the cause; true 2 by 2 binning caught 24 more frames, mostly good ones in crowded fields.
- **Verdict:** Not judged.
- **Limits:** One color camera at one focal length (405 mm).
- **To tune for your setup:** Raise the allowance if good frames of a color camera are moved; lower it if a bad frame escapes. Re-run the quarantine preview.
- **Regenerate with:** No script; measured by hand on 2026-10-12 and 2026-10-13.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_frame_quarantine.py`.
- **Raw record:** None kept.

### STK-03

**The quarantine cap: at most a quarter of a session**

- **Claim:** On normal sessions the quarantine moves at most about 13% of a batch, so a cap of 25% leaves room for a bad stretch while stopping it from gutting a session whose limits do not suit the equipment.
- **Settings it supports:**
  - code constant `MAXIMUM_QUARANTINED_FRACTION` = 0.25 in `pipelines/stacking/pre_processing/frame_quarantine.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MAXIMUM_QUARANTINED_FRACTION -->
- **Status:** Partly measured. The normal share is measured, but no session with a genuinely bad night was available to show where the cap should sit.
- **Data you need:** Your whole library of imaging targets, or at least ten sessions.
- **Procedure:**
  1. Run the quarantine preview (STK-01, step 2) on every imaging target.
  2. For each batch, divide the number of frames it would move by the batch size.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for target in astro.targets.list():
      preview = astro.quality.frame_quality(target.id, kind="quarantine_preview", limit=300)
      if preview.frames_checked:
          print(target.id, preview.would_move_total, preview.frames_checked, preview.notes)
  ```

- **Acceptance criterion:** The cap is well above the largest share moved in any batch.
- **What to look at:** The largest share moved in any batch, as a fraction. The cap should be well above it. A `notes` line saying a batch was left alone because too many frames would move points to limits that do not suit the equipment.
- **Our result:** A dry run on the library picked 4 of 56 frames (7%) of the 2026-10-02 target, 4 of 30 (13%) and 5 of 40 (12%) of two M 27 sessions, and none in the other six batches. A quarter is about twice the largest share.
- **Verdict:** Not judged.
- **Limits:** Nine batches, one camera.
- **To tune for your setup:** If a real cloudy night is refused as a whole, raise the cap; if a mismatched limit moves a third of a good session, keep or lower it and fix the limit (STK-01) instead. The `frame_quarantine` gate (`QualityDiagnostics.stack_quality`, or the stack's gate list) reports the measured share and the limit.
- **Regenerate with:** No script; dry run by hand (date not recorded).
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_frame_quarantine_gate.py`.
- **Raw record:** None kept.

### STK-04

**The background split detector**

- **Claim:** The largest gap between sorted sky levels, divided by the spread within the groups on either side, stays below 1 on normal nights and rises above 6 when cloud arrives, so a ratio of 4 separates them.
- **Settings it supports:**
  - code constant `DEFAULT_GAP_RATIO_THRESHOLD` = 4.0 in `pipelines/stacking/pre_processing/background_homogeneity.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.background_homogeneity.DEFAULT_GAP_RATIO_THRESHOLD -->
  - on/off: config key `[Processing.Siril] background_homogeneity_check_enabled` (default `true`). Relation: sanity check only.
- **Status:** Partly measured. The two ranges come from the unit tests, whose sky levels are made up; no record of a real-session measurement exists.
- **Data you need:** One clear session and one session where cloud arrived suddenly, each with at least three frames of one exposure length.
- **Procedure:**
  1. Read each frame's sky level with `QualityDiagnostics.frame_quality(target, kind="raw_check")`.
  2. Pass the levels to the detector and read the gap ratio it reports.

  ```python
  from astrometricslib import Astrometrics
  from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import detect_background_split

  astro = Astrometrics()
  # One night, one exposure length: narrow the selection with filter_name, since and until.
  check = astro.quality.frame_quality(
      "NGC 2403",
      kind="raw_check",
      filter_name="L",
      since="2026-03-14T18:00",
      until="2026-03-15T06:00",
      limit=300,
  )
  levels = [row["sky_median_adu"] for row in check.frames if row.get("sky_median_adu") is not None]
  print(detect_background_split(levels, gap_ratio_threshold=0.0))
  ```

  A threshold of 0 makes the detector report the largest gap and its `gap_ratio` whatever its size, so you can see where your clear and clouded nights fall. The selection must hold one exposure length, as the stacker's check does.

  `detect_background_split` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.background_homogeneity.detect_background_split -->
- **Acceptance criterion:** Clear nights stay well below the threshold and clouded ones well above it.
- **What to look at:** The gap ratio (no units). Clear nights should stay well below the threshold and clouded ones well above.
- **Our result:** Measured value not recorded; no real-session numbers exist.
- **Verdict:** Not judged.
- **Limits:** Synthetic only. A slow build-up of cloud leaves no single gap and passes by design.
- **To tune for your setup:** Measure the ratio on your clear and clouded sessions and place the threshold between them.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_background_homogeneity_by_exposure.py` and `test_background_homogeneity_tasks.py`. Their made-up sky levels give a ratio below 1.0 for normal conditions and above 6.0 for sudden cloud.
- **Raw record:** None kept.

### STK-05

**How noisy a master flat may be**

- **Claim:** A master flat whose relative noise is at most 0.5% adds under 3% to the variance of a typical stack's sky noise. A noisier flat leaves a visible fixed pattern, and blurring it removes most of that pattern.
- **Settings it supports:**
  - code constant `MAXIMUM_FLAT_NOISE_FRACTION` = 0.005 in `pipelines/stacking/pre_processing/flat_calibration.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.MAXIMUM_FLAT_NOISE_FRACTION -->
- **Status:** Partly measured. The stack noise level and the effect of one bad flat are measured; the 3% variance figure follows from them by arithmetic.
- **Data you need:** A flat set (ideally a single flat and a set of 20 or more), and an undithered stack (frames not moved between exposures) of a faint field calibrated with each.
- **Procedure:**
  1. Measure the flat set the way the stacker does.
  2. Measure the pixel noise of the stack as a fraction of its sky level.
  3. Restack after changing the flats, and compare the two stacks.

  ```python
  import glob

  from astrometricslib import Astrometrics
  from astrometricslib.pipelines.stacking.pre_processing.flat_calibration import assess_flats

  astro = Astrometrics()
  flats = sorted(glob.glob("/path/to/flats/L/*.fits"))
  assessment = assess_flats(flats, camera="ZWO ASI533MM Pro")
  print(assessment.frame_count, assessment.noise_fraction, assessment.smoothing_sigma_pixels, assessment.issues)
  report = astro.quality.stack_quality("M 101", compare_to="previous")
  print(report.comparison.before.noise_fraction, report.comparison.after.noise_fraction)
  ```

  `assess_flats` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.assess_flats --> `CalibrationCatalog.assess_flats` is the public form for flats already in the calibration library. <!-- impl: astrometricslib.api.processing.CalibrationCatalog.assess_flats --> The comparison uses `QualityDiagnostics.stack_quality`. <!-- impl: astrometricslib.api.quality.QualityDiagnostics.stack_quality -->
- **Acceptance criterion:** If the flat's noise squared is small next to the stack's noise squared, the flat adds nothing visible.
- **What to look at:** `noise_fraction` of the master flat (standard deviation over mean; 0.005 is 0.5%). The stack's `noise_fraction` (pixel-to-pixel noise over the sky level; lower is cleaner). If the flat's noise squared is small next to the stack's noise squared, the flat adds nothing visible.
- **Our result:** The sky noise of the ZWO ASI533MM Pro stacks of 20 targets was 3% to 6% of the sky level. The master flat of the single-flat library measured 4.5%, and its fixed pattern made up 86% of the fine-scale noise of the undithered M 101 stack. Smoothing that flat halved the stack's noise. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** One bad flat on one stack. A dithered stack hides the pattern, so the same flat would look harmless there.
- **To tune for your setup:** If your stacks are much less noisy than 3% of the sky (long, dark-site stacks), lower the limit so the flat stays well below the stack noise. Re-run the comparison after each change.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_flat_calibration.py`.
- **Raw record:** None kept.

### STK-06

**The width of a dark temperature slot**

- **Claim:** Darks taken more than a few degrees from the lights over- or under-subtract, and mixing darks of two temperatures into one master gives a stack with many negative pixels. A slot 3 °C wide keeps such darks apart.
- **Settings it supports:**
  - code constant `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` = 3.0 in `drivers/calibration_library.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.drivers.calibration_library.DEFAULT_DARK_TEMPERATURE_TOLERANCE_C -->
- **Status:** Partly measured. Two real incidents show the harm of mixing temperatures; the 3 °C width itself is not fitted to the camera's dark-current curve.
- **Data you need:** Darks of one exposure and gain at several sensor temperatures (for example 0, -5, -10 and -15 °C), and lights at one of them.
- **Procedure:**
  1. Ask the calibration library which darks it would pick for your lights.
  2. Stack once with darks from the lights' own slot and once with darks a slot away, and read the negative-pixel warning and the stack noise.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  match = astro.processing.calibration.query(detail="target_match", target="Arcturus")
  print(match)
  result = astro.processing.stack("Arcturus", kind="spectral", force=True)
  metrics = result.quality_summary.stacking_metrics
  print(metrics.negative_pixel_max_percent, metrics.calibration_mismatch_flags)
  ```

  `CalibrationCatalog.query` <!-- impl: astrometricslib.api.processing.CalibrationCatalog.query --> and `ProcessingPipelines.stack` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.stack -->.
- **Acceptance criterion:** `negative_pixel_max_percent` is 0 or empty.
- **What to look at:** `negative_pixel_max_percent` (the worst "many negative pixels" warning Siril printed, in percent; 0 or empty is good). The dark-current difference between neighboring slots, measured as the mean of a master dark minus the mean of a bias, in ADU.
- **Our result:** Arcturus's 0.5 s, gain-0 darks clustered near 0 °C and -10 °C, while its lights drifted from -2.9 °C to -10.7 °C; Siril reported 57% to 64% negative pixels with the mixed master. A library file of 2026-10-03 had no temperature in its keys; without the slotting rule, -12 °C lights received a master of 61 darks at 0 °C and 30 at -10 °C, with no flag.
- **Verdict:** Not judged.
- **Limits:** Two incidents; no dark-current curve measured.
- **To tune for your setup:** Measure your camera's dark current against temperature. If it doubles in fewer than about 3 °C, narrow the slot; if your cooler holds within 1 °C and dark current changes slowly, the slot can stay as it is.
- **Regenerate with:** No script.
- **Guarded by tests:** `test_a_library_saved_before_temperatures_does_not_pool_them` in `astrometricslib/drivers/test/test_calibration_matching.py` reproduces the 2026-10-03 case.
- **Raw record:** None kept.

### STK-07

**Noise weighting for mixed exposure lengths**

- **Claim:** For sky-limited frames, equal weights leave a stack noisier than the best weighting by the factor P = √(mean(1/t) × mean(t)) for exposure times t, and Siril's noise weighting recovers most of that loss.
- **Settings it supports:**
  - code constant `MINIMUM_PENALTY_TO_WEIGHT` = 1.05 in `pipelines/stacking/pre_processing/exposure_weighting.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.MINIMUM_PENALTY_TO_WEIGHT -->
  - config key `[Processing.Siril] stack_weight` (default empty, which lets the rule choose). Relation: sanity check only.
- **Status:** Partly measured. The benefit was measured on one stack; the 1.05 switch-on point is a design estimate.
- **Data you need:** An imaging target with frames of at least two exposure lengths that differ by a factor of 2 or more, all sky-limited (the sky noise is larger than the read noise).
- **Procedure:**
  1. Compute P from the exposure times.
  2. Stack with the weighting the rule picks, writing to a scratch file.
  3. Stack again with equal weights. The public API always lets the rule decide when no weight is configured, so raise `MINIMUM_PENALTY_TO_WEIGHT` in the running process for this one call.
  4. Compare the pixel noise of the two stacks.

  ```python
  from astrometricslib import Astrometrics
  from astrometricslib.pipelines.stacking.pre_processing import exposure_weighting
  from astrometricslib.pipelines.stacking.processing.exposure_groups import frame_exposure_seconds

  astro = Astrometrics()
  target = astro.targets.get("M 27")
  frames = [frame for frame in target.frames if frame.filter.name in ("L", "NONE")]
  print(exposure_weighting.equal_weight_noise_penalty([frame_exposure_seconds(frame) for frame in frames]))
  weighted = astro.processing.stack(target, frames=frames, force=True, output_file="/tmp/m27_weighted.fits")
  exposure_weighting.MINIMUM_PENALTY_TO_WEIGHT = 100.0  # equal weights for this run only
  equal = astro.processing.stack(target, frames=frames, force=True, output_file="/tmp/m27_equal.fits")
  report = astro.quality.stack_quality(weighted.stacked_path, compare_to=equal.stacked_path)
  print(report.comparison.changes["noise_fraction"])
  ```

  `equal_weight_noise_penalty` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.equal_weight_noise_penalty --> `frame_exposure_seconds` (internal function, not part of the public API; may change) reads each frame's exposure the way the stacker does. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.frame_exposure_seconds -->
- **Acceptance criterion:** The measured gain approaches 1 − 1/P.
- **What to look at:** P (no units; 1.17 means 17% more noise with equal weights) and the relative change in `noise_fraction` between the two stacks (a negative change means the weighted stack is quieter). The measured gain should approach 1 − 1/P.
- **Our result:** M 27, 26 frames of 30 s, 33 of 60 s, 22 of 120 s and 1 of 300 s: P = 1.17. Noise weighting (`-weight=noise`) lowered the measured pixel noise by 13% (date not recorded).
- **Verdict:** Not judged.
- **Limits:** One stack. Frames limited by read noise rather than sky noise follow a different rule.
- **To tune for your setup:** If small exposure differences in your sessions trigger weighting with no visible gain, raise `MINIMUM_PENALTY_TO_WEIGHT`; to force a weighting, set `stack_weight`.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_exposure_weighting.py`.
- **Raw record:** None kept.

### STK-08

**Pixel rejection limits for small stacks (simulation)**

- **Claim:** For stacks of 5 to 15 frames, the plain Chauvenet limit throws out several percent of good values. A floor of 2.5 on the high limit, with the low limit 0.5 looser, cuts that loss by more than half, at the cost of leaving some weak outliers in.
- **Settings it supports:**
  - config key `[Processing.Siril] stack_rejection_sigma_floor` (default 2.5). Relation: confirmed by this entry.
  - config key `[Processing.Siril] stack_rejection_low_extra_sigma` (default 0.5). Relation: confirmed by this entry.
  - config key `[Processing.Siril] rejection_sigma_mode` (default `adaptive`). Relation: sanity check only.
  - the rule itself: `rejection_bounds` <!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds --> and `chauvenet_sigma` <!-- impl: astrometricslib.utilities.rejection_thresholds.chauvenet_sigma --> (internal functions, not part of the public API; may change). Relation: derived from this entry.
- **Status:** Partly measured. The numbers come from a simulation of pure Gaussian noise that models Siril's Winsorized sigma clipping approximately; there is no measurement on real stacks of 5 to 15 frames.
- **Data you need:** None for the simulation. To check it on real data, a session of 40 or more frames of one field from which you can draw subsets of 5, 8 and 15 frames.
- **Procedure:**
  1. Run the simulation for 5σ and 8σ outliers.

     ```bash
     python -m astrometricslib.scripts.rejection_small_n_check --outlier-sigma 5
     python -m astrometricslib.scripts.rejection_small_n_check
     ```

     The script draws 100,000 pixels of Gaussian noise per frame count (5, 8, 15, 40), applies each rule with the median as center and a Winsorized spread (values beyond 1.5 standard deviations pulled in, repeated, times 1.134). It adds one outlier to one frame of each pixel. <!-- impl: astrometricslib.scripts.rejection_small_n_check -->
  2. On real data, stack subsets of N frames at the default limits and at the plain Chauvenet limit, with the rejection map on, and compare the rejected share and star width.

     ```python
     from astrometricslib import Astrometrics
     from astrometricslib.utilities.rejection_thresholds import chauvenet_sigma

     astro = Astrometrics()
     target = astro.targets.get("M 13")
     subset = [frame for frame in target.frames if frame.filter.name == "L"][:8]
     for limits in (None, (chauvenet_sigma(8), chauvenet_sigma(8))):
         result = astro.processing.stack(
             target,
             frames=subset,
             rejection_sigma=limits,
             generate_rejmap=True,
             force=True,
             output_file="/tmp/m13_n8.fits",
         )
         quality = astro.quality.stack_quality(result.stacked_path, include=["fwhm", "rejected_fraction"])
         print(limits, quality.fwhm_px, quality.rejected_fraction)
     ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** In the simulation, "good rej %" (the share of good samples thrown out; lower is better) and "caught %" (how often the outlier is removed; higher is better). On real data, the rejected fraction (0 to 1) and the stack's star width in pixels (smaller is sharper).
- **Our result:** Re-run on 2026-10-15 with the default seed; the output matches the earlier figures exactly. Good samples rejected (Winsorized spread):

  | Frames | Chauvenet limit on both sides | Floor 2.5 on both sides | Default (low 3.0, high 2.5) |
  | --- | --- | --- | --- |
  | 5 | 7.4% | 3.0% | 2.4% |
  | 8 | 5.4% | 2.1% | 1.6% |
  | 15 | 3.7% | 1.9% | 1.3% |
  | 40 | 1.5% | 1.5% | 0.9% |

  Share of one outlier caught, Chauvenet limit against the floor:

  | Frames | 5σ outlier | 8σ outlier |
  | --- | --- | --- |
  | 5 | 83% to 60% | 98.5% to 89% |
  | 8 | 95% to 84% | 100% to 99.7% |
  | 15 | 98% to 96% | 100% to 100% |

  With a plain sample standard deviation instead of the Winsorized estimate, a stack of 5 or 8 frames cannot reject anything at 2.5. No one of n values can lie more than (n−1)/√n sample standard deviations from their mean, which is 1.79 for 5 values and 2.47 for 8.
- **Verdict:** Not judged.
- **Limits:** The Winsorized estimate is a simplified model of Siril's, so the figures show the size of the effect, not Siril's exact output. The noise is Gaussian only, so the simulation cannot show the benefit of the looser low limit on real data, where the bad values are mostly bright. The 2.5 floor and the 0.5 extra are design choices, not fitted values.
- **To tune for your setup:** If small stacks keep satellite trails, lower `stack_rejection_sigma_floor`; if they look noisier than expected, raise it. Re-run step 2 on subsets of your own frames.
- **Regenerate with:** `astrometricslib/scripts/rejection_small_n_check.py` (`python -m astrometricslib.scripts.rejection_small_n_check [--pixels N] [--outlier-sigma S] [--seed K]`).
- **Guarded by tests:** `astrometricslib/utilities/test/test_rejection_thresholds.py`.
- **Raw record:** None kept.

### STK-09

**Rejection limits on large real stacks: no loss of sharpness**

- **Claim:** On stacks of 40 or more frames, lowering the rejection limit from 3.0 to 2.5 does not change the stacked star width, so the frame-count rule (which lands near 2.5 at 40 frames) costs no sharpness there. The rejected share stays below about 10% even on a cloud-affected session.
- **Settings it supports:**
  - config key `[Processing.Siril] rejection_sigma_mode` (default `adaptive`). Relation: confirmed by this entry.
  - config keys `rejection_sigma_low` and `rejection_sigma_high` in `[Processing.Siril]` (default 3.0 each, used only in `fixed` mode). Relation: sanity check only.
- **Status:** Partly measured. Three real targets with 40 or more frames; the result tables were written to the run log folder and are not kept in the repository.
- **Data you need:** A monochrome luminance session of 40 or more frames of one field, ideally one clear session and one partly clouded.
- **Procedure:** Run the sweep. It stacks the target's luminance frames once for each pair of limits (2.0 to 4.0) and each Siril sharpness filter (`-filter-wfwhm` off, 90%, 80%), then measures the stacked star width and the rejected share with `QualityDiagnostics.stack_quality(path, include=["fwhm", "rejected_fraction"])`.

  ```bash
  python -m astrometricslib.scripts.rejection_threshold_analysis "M 81" --date 2026-05-23
  ```

  The camera name is fixed in the script (`ZWO ASI 533MM Pro`); edit it for another camera. <!-- impl: astrometricslib.scripts.rejection_threshold_analysis -->
- **Acceptance criterion:** A good limit gives the smallest width without a jump in the rejected share.
- **What to look at:** `fwhm_px` (stacked star width in pixels; smaller is sharper) and `rej_frac` (mean share of frames rejected per pixel, 0 to 1) for each row. A good limit gives the smallest width without a jump in the rejected share.
- **Our result:** On M 81, M 13 and NGC 2403 (40 or more frames each) the stacked width did not differ between sigma 2.5 and 3.0. The rejected share never went above about 10%, even on NGC 2403's cloud-affected 70-frame session at the loosest limits tried. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** No stacks of 5 to 15 frames were swept, so STK-08 rests on simulation alone. The raw tables are not kept.
- **To tune for your setup:** Run the sweep on your own sessions; if a lower limit sharpens your stacks, use `fixed` mode with that pair, or change the floor.
- **Regenerate with:** `astrometricslib/scripts/rejection_threshold_analysis.py` (`python -m astrometricslib.scripts.rejection_threshold_analysis [TARGET] [--date YYYY-MM-DD]`; writes `rejection_threshold_analysis_<target>[_<date>].json` to the configured logs folder).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### STK-10

**The fewest frames left after the sharpness filter**

- **Claim:** On one session, stacks of 5 to 20 frames got steadily worse as frames were removed, with no sudden failure at 5, so a floor of 5 surviving frames does not produce a broken stack.
- **Settings it supports:**
  - code constant `DEFAULT_MINIMUM_SURVIVING_FRAMES` = 5 in `utilities/stack_filter_floor.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.utilities.stack_filter_floor.DEFAULT_MINIMUM_SURVIVING_FRAMES -->
  - the loosening order `FILTER_WFWHM_LOOSENING_LADDER` ("80%", "90%", off) in the same file. Relation: sanity check only. <!-- impl: astrometricslib.utilities.stack_filter_floor.FILTER_WFWHM_LOOSENING_LADDER -->
  - config key `[Processing.Siril] filter_wfwhm_percentile` (default empty, no filter). Relation: sanity check only.
- **Status:** Partly measured. One session, and no failure point was found to set the value against.
- **Data you need:** One session of 20 or more luminance frames of one field.
- **Procedure:** Stack the first N frames for N = 5, 6, 8, 10, 15 and 20, with the default rejection limits and no sharpness filter, and measure each stack.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  target = astro.targets.get("M 13")
  frames = [frame for frame in target.frames if frame.filter.name == "L"]
  for count in (5, 6, 8, 10, 15, 20):
      result = astro.processing.stack(
          target, frames=frames[:count], generate_rejmap=True, force=True, output_file=f"/tmp/m13_{count}.fits"
      )
      quality = astro.quality.stack_quality(result.stacked_path, include=["fwhm", "rejected_fraction"])
      print(count, quality.fwhm_px, quality.rejected_fraction)
  ```

  `resolve_filter_wfwhm_with_floor` (internal function, not part of the public API; may change) shows which filter a given frame count would really get. <!-- impl: astrometricslib.utilities.stack_filter_floor.resolve_filter_wfwhm_with_floor -->
- **Acceptance criterion:** A smooth curve, with no cliff, means the floor is not critical.
- **What to look at:** Stacked star width in pixels and rejected fraction against N. Look for a cliff; a smooth curve means the floor is not critical.
- **Our result:** M 13 luminance, one session: stacked width rose smoothly from 3.72 px (N = 20) to 4.07 px (N = 5, about 9% worse), and the rejected share from 4.8% to 8.2%. The raw per-N table was not kept. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** One session; no cliff found, so the exact value is not calibrated.
- **To tune for your setup:** If your small stacks fall apart below some N, set the floor at that N.
- **Regenerate with:** No script; measured by hand.
- **Guarded by tests:** `astrometricslib/utilities/test/test_stack_filter_floor.py`.
- **Raw record:** None kept.

### STK-11

**Stacking each exposure length on its own**

- **Claim:** Stacking frames of very different exposure lengths in one batch, with Siril's normalization and sigma clipping, throws away the longest frames' values as outliers. Stacking each length on its own and combining the group stacks gives a much less noisy result.
- **Settings it supports:**
  - the split itself (`split_frames_by_exposure`) <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.split_frames_by_exposure --> and the combination (`combine_exposure_group_images`). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.combine_exposure_group_images -->
  - code constant `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` = 5 in `pipelines/stacking/processing/exposure_groups.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.MINIMUM_FRAMES_PER_EXPOSURE_GROUP -->
- **Status:** Measured. On one session (the comparison with a plain sum).
- **Data you need:** A bracketed session: many frames of one bright star at several exposure lengths (for example 0.5, 1, 2, 3 and 5 s), slitless spectra or imaging.
- **Procedure:**
  1. Stack the session with the pipeline (it splits by exposure length).
  2. Stack the same frames as one batch with Siril yourself, and make a plain sum of the calibrated frames.
  3. Extract the same faint star from each and compare the noise of its spectrum (or its flux).

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.stack("Vega", kind="spectral", force=True)
  for group in result.quality_summary.stacking_metrics.exposure_groups:
      print(group.exposure_seconds, group.frames_stacked, group.gain_mid_range, group.left_out_reason)
  ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Relative noise of the extracted spectrum: 1.4826 × the median absolute deviation of neighboring-point differences, divided by √2 and by the median, over 4200 to 7000 Å. It includes real spectral structure, so compare only like with like.
- **Our result:** On the Vega session (140 frames, 0.5 to 5 s, 2026-09-20), the single-batch recipe made a faint star's spectrum about 8 times noisier than a plain sum of the same frames. With per-group stacking, on the same star positions: Vega 0.0039 to 0.0037, Alcor 0.0055 to 0.0033 relative noise (old stack to new). Alnath gave no usable spectrum either way (1.25 and 0.82).
- **Verdict:** Not judged.
- **Limits:** One bracketed session for the 8-times figure. The minimum of five frames per group is not tested.
- **To tune for your setup:** Nothing to tune for the split. If your short groups have fewer than five frames, decide whether to fold them into a neighbor (the default) or to take more frames.
- **Regenerate with:** No script; see the raw record.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_exposure_groups.py`.
- **Raw record:** `documentation/evidence/stack_exposure_groups_20260920.json` (sections `spectrum_comparison_same_star_position` and `relative_noise_definition`).

### STK-12

**Registration retries for spectral frames**

- **Claim:** Siril's standard star detection registers bracketed spectral frames well on most sessions, but fails on some short groups and on fields with one or two stars. Retrying with relaxed detection, then with phase correlation, recovers those groups.
- **Settings it supports:**
  - code constant `MINIMUM_REGISTERED_FRACTION` = 0.9 in `pipelines/stacking/stack_runner.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.stack_runner.MINIMUM_REGISTERED_FRACTION -->
  - the order of attempts `SPECTRAL_STAR_DETECTION_ORDER` in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.stack_runner.SPECTRAL_STAR_DETECTION_ORDER -->
  - the phase-correlation fallback `align_calibrated_frames`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.processing.spectral_frame_alignment.align_calibrated_frames -->
- **Status:** Partly measured. The registration counts are measured on four sessions; the 0.9 limit is a design estimate.
- **Data you need:** Slitless spectral sessions of a bright single star, a double star, and a faint star, with several exposure lengths each.
- **Procedure:** Stack each session and read how many frames registered in each group.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.stack("Alcor", kind="spectral", force=True)
  for group in result.quality_summary.stacking_metrics.exposure_groups:
      print(group.exposure_seconds, group.frames_submitted, group.frames_stacked)
  report = astro.quality.stack_quality("Alcor", kind="spectral", include=["registration"])
  print(len(report.registration or []))
  ```

  The job log names the detection that won for each group.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The share of frames registered per group (frames stacked over frames submitted, 0 to 1). A group below 0.9 triggers the retry.
- **Our result:** From 2026-09-20: Vega, 5 groups, standard detection registered 140 of 140 frames (relaxed detection alone had registered 94). Alcor: 133 registered, 5 failed; two groups fell below 90% with standard detection and were retried with relaxed. Alnath: 56 registered, 7 failed; standard detection registered no frame of the 0.1 s group, relaxed was used, and one group stayed at 77%. Albireo (a close double): both Siril detections left most frames of some groups unregistered and one group failed outright, which is why phase correlation is the last fallback.
- **Verdict:** Not judged.
- **Limits:** Four sessions. The 0.9 limit tolerates one lost frame in ten; it has not been tuned.
- **To tune for your setup:** If good sessions trigger retries, lower `MINIMUM_REGISTERED_FRACTION`; if a registration that lost a third of the frames is accepted, raise it.
- **Regenerate with:** No script; see the raw record.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_siril_stacking.py` and `test_spectral_frame_alignment.py`.
- **Raw record:** `documentation/evidence/stack_exposure_groups_20260920.json` (section `registration`).

### STK-13

**When a shift between two group stacks is trusted**

- **Claim:** After moving one group stack onto another, real pairs of stacks of the same field correlate at 0.55 or more, and stacks of different fields at 0.09 or less, so a correlation of 0.3 separates true shifts from false ones.
- **Settings it supports:**
  - code constant `MINIMUM_ALIGNMENT_CORRELATION` = 0.3 in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.MINIMUM_ALIGNMENT_CORRELATION -->
- **Status:** Measured. Same-field and different-field pairs of group stacks from several targets.
- **Data you need:** Pairs of stacks of the same field (two exposure groups, or two halves of one group) and pairs of stacks of different fields.
- **Procedure:**

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.processing.group_alignment import measure_alignment

  reference = np.asarray(fits.getdata("/path/to/groups/NGC_2244_300s.fits", memmap=False), dtype=float)
  other = np.asarray(fits.getdata("/path/to/groups/NGC_2244_60s.fits", memmap=False), dtype=float)
  result = measure_alignment(reference, other)
  print(result.shift_rows_pixels, result.shift_columns_pixels, result.correlation, result.trusted)
  ```

  `measure_alignment` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.measure_alignment --> The group stacks are in the `groups` folder beside each combined stack.
- **Acceptance criterion:** The threshold sits in the gap between same-field and different-field pairs.
- **What to look at:** `correlation` (no units; 1 for identical images, near 0 for unrelated ones). The threshold should sit in the gap between same-field and different-field pairs.
- **Our result:** Same-field pairs (NGC 2244, NGC 2903, NGC 2403, NGC 1893, and the halves of one group): 0.55 to 0.96. Different-field pairs: 0.03 to 0.09. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** The same-field pairs are all from one camera. Nikon D5300 pairs that correlation refused (STK-15) scored 0.01 to 0.26, below the threshold, although they were the same field; the star-pattern fallback handles them.
- **To tune for your setup:** Measure the correlation of your own same-field pairs; keep the threshold below the lowest and above every different-field pair.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_group_alignment.py`.
- **Raw record:** None kept.

### STK-14

**Refining a group shift with a rotation and a scale**

- **Claim:** Stacks of different nights differ by a small rotation and scale that a shift cannot remove. Fitting a shift, a rotation and a scale to a few hundred matched stars brings the leftover offset from about a pixel to 0.05 pixels.
- **Settings it supports:**
  - code constant `REFINEMENT_MINIMUM_PAIRS` = 30 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.REFINEMENT_MINIMUM_PAIRS --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `REFINEMENT_MAX_ROTATION_DEGREES` = 0.5 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.REFINEMENT_MAX_ROTATION_DEGREES --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `REFINEMENT_MAX_SCALE_DEVIATION` = 0.005 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.REFINEMENT_MAX_SCALE_DEVIATION --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `REFINEMENT_MAX_RESIDUAL_PIXELS` = 0.5 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.REFINEMENT_MAX_RESIDUAL_PIXELS --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `REFINEMENT_STAR_COUNT` = 400 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.REFINEMENT_STAR_COUNT --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Partly measured. Three pairs of group stacks from two targets.
- **Data you need:** A target stacked from two or more nights at two exposure lengths, so that the group stacks come from different nights.
- **Procedure:**

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.processing.group_alignment import (
      measure_alignment,
      refine_alignment_with_stars,
  )

  reference = np.asarray(fits.getdata("/path/to/groups/M_27_127.8s.fits", memmap=False), dtype=float)
  other = np.asarray(fits.getdata("/path/to/groups/M_27_60s.fits", memmap=False), dtype=float)
  shift_only = measure_alignment(reference, other)
  refined = refine_alignment_with_stars(reference, other, shift_only)
  print(refined.rotation_degrees, refined.scale, refined.star_pairs, refined.residual_pixels)
  ```

  `refine_alignment_with_stars` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.refine_alignment_with_stars --> The same numbers for a finished stack are in its group manifest (`alignment_rotation_degrees`, `alignment_scale`, `alignment_star_pairs`, `alignment_residual_pixels`).
- **Acceptance criterion:** The limits sit about ten times above what real pairs show.
- **What to look at:** `residual_pixels` (root mean square distance of matched stars after the fit, in pixels; lower is better), `rotation_degrees` and `scale`. The limits should sit about ten times above what real pairs show.
- **Our result:** M 57 (2026-10-03), 30 s against 60 s: rotation -0.040°, scale +0.022%. M 27, 30 s and 60 s against 127.8 s: rotations 0.053° and -0.004°, scales -0.012% and -0.052%. With the best shift alone, stars were still 0.86 px (M 57) and 1.09 and 0.61 px (M 27) off, up to 1.9 px toward the edges. The fit, on 368 to 375 matched stars (92% to 94% of the 400 brightest), left 0.05 to 0.06 px.
- **Verdict:** Not judged.
- **Limits:** Three pairs. The limits were chosen on them alone.
- **To tune for your setup:** If your nights differ by larger rotations (a camera removed and refitted), raise `REFINEMENT_MAX_ROTATION_DEGREES`; the star-pattern fallback (STK-15) also covers large turns.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_group_alignment.py`.
- **Raw record:** None kept.

### STK-15

**The star-pattern fallback for group alignment**

- **Claim:** When correlation fails, matching triangles of stars finds the move between two group stacks with no starting guess, including the half turn of a meridian flip, and the stars confirm it.
- **Settings it supports:**
  - code constant `PATTERN_MAX_RESIDUAL_PIXELS` = 1.5 in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.PATTERN_MAX_RESIDUAL_PIXELS -->
  - it shares `REFINEMENT_MINIMUM_PAIRS` and `REFINEMENT_MAX_SCALE_DEVIATION` with STK-14. Relation: sanity check only.
- **Status:** Partly measured. Five targets from one camera.
- **Data you need:** Group stacks that correlation refuses, such as stacks from before and after a meridian flip, or from a color camera.
- **Procedure:**

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.processing.group_alignment import find_alignment_from_star_patterns

  reference = np.asarray(fits.getdata("/path/to/groups/NGC_2024_120s.fits", memmap=False), dtype=float)
  other = np.asarray(fits.getdata("/path/to/groups/NGC_2024_60s.fits", memmap=False), dtype=float)
  match = find_alignment_from_star_patterns(reference, other)
  if match is not None:
      print(match.rotation_degrees, match.scale, match.star_pairs, match.residual_pixels)
  ```

  `find_alignment_from_star_patterns` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.find_alignment_from_star_patterns -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether a match is found, its rotation in degrees, its scale (1 means equal), the number of star pairs and the leftover in pixels.
- **Our result:** On 2026-10-13, on Nikon D5300 group stacks that correlation had refused (correlation 0.01 to 0.26): M 106 (turn -0.40°), NGC 7023 (+0.16°), M 51 300 s (-6.28°), NGC 2024 (-179.59°) and NGC 6992 (+179.53°); scales 0.9996 to 1.0014. Leftovers: 0.13 px (NGC 7023, 249 pairs), 0.31 px (NGC 2024, 350; NGC 6992, 193), 0.44 px (M 106, 249) and 1.20 px (M 51 300 s, 223). M 106's leftover is smooth lens distortion; M 51 300 s's is scatter of star centers in a soft stack.
- **Verdict:** Not judged.
- **Limits:** One camera, five targets. The 1.5 px limit sits just over the largest leftover seen.
- **To tune for your setup:** If a good pattern match on a soft stack is refused, raise `PATTERN_MAX_RESIDUAL_PIXELS` while keeping it well under the 3 px pairing radius.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_group_alignment_star_patterns.py`.
- **Raw record:** None kept.

### STK-16

**Group alignment and combination compared with Siril's own commands**

- **Claim:** The pipeline's own group alignment leaves group stacks within about 0.05 pixels of the reference, while Siril's registration of the same group stacks is 0.06 to 3 pixels off. Siril's stacking of the aligned groups also has no per-pixel saturation handling. These steps therefore stay in Python.
- **Settings it supports:**
  - the design choice to align (`align_images_to_reference`) <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.align_images_to_reference --> and combine (`combine_exposure_group_images`) group stacks in the library rather than in Siril. Relation: derived from this entry.
- **Status:** Measured. Group stacks compared with Siril's own registration and stacking.
- **Data you need:** Targets already stacked in two or more exposure groups (their `groups` folder and manifest), including one bright object that saturates the longest group. Siril installed.
- **Procedure:** Run the comparison script. For each target it aligns the non-reference group stacks with the library and with Siril (`register -transf=shift`). It measures the leftover shift by fine phase correlation and by matched star centers (or the zero-order star for spectra). It compares a Siril noise-weighted stack of the aligned groups with the pipeline's combined stack at the pixels the longest group saturates. It changes nothing in the library.

  ```bash
  python -m astrometricslib.scripts.compare_group_steps_with_siril --targets "M 81" "Moon" "Albireo"
  ```

  <!-- impl: astrometricslib.scripts.compare_group_steps_with_siril -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The phase-correlation residual and the star-center offset of each aligned image, in pixels (closer to 0 is better), and, for the combination, the ratio of Siril's blend to the pipeline's stack at saturated pixels (near 1 means no loss).
- **Our result:** On the groups measured, the library placed frames within 0.05 pixels, against 0.06 to 3 pixels for Siril's registration. The target list and date of that run, and the combination ratios, are not recorded.
- **Verdict:** Not judged.
- **Limits:** The run's output was not kept.
- **To tune for your setup:** Nothing to tune; re-run the script if you consider replacing a step with Siril's.
- **Regenerate with:** `astrometricslib/scripts/compare_group_steps_with_siril.py` (`python -m astrometricslib.scripts.compare_group_steps_with_siril --targets NAME [NAME ...] [--work-dir DIR]`).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### STK-17

**Finding the zero-order star in a spectral stack**

- **Claim:** The spectroscopy target is framed near the image center, so the brightest smoothed spot within 150 pixels of the center is the zero-order star, unless a spot more than 40 pixels away reaches half its brightness.
- **Settings it supports:**
  - code constant `ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS` = 150 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `ZERO_ORDER_RIVAL_SEPARATION_PIXELS` = 40 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.ZERO_ORDER_RIVAL_SEPARATION_PIXELS --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `ZERO_ORDER_MAX_RIVAL_FRACTION` = 0.5 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.ZERO_ORDER_MAX_RIVAL_FRACTION --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Partly measured. The 150-pixel window rests on the Vega nights; the 40-pixel distance and the one-half fraction are not validated.
- **Data you need:** Slitless spectral frames of a single bright star, centered as you usually center targets.
- **Procedure:**

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.processing.group_alignment import find_zero_order_position

  plane = np.asarray(fits.getdata("/path/to/Vega_spectral_stack.fits", memmap=False), dtype=float)
  print(plane.shape, find_zero_order_position(plane))
  ```

  `find_zero_order_position` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.find_zero_order_position -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The distance of the found position from the image center, in pixels, over many frames and nights; and the brightest other spot as a fraction of the star.
- **Our result:** On the Vega nights the star sat within 35 pixels of the center; 150 pixels covers four times that and stays well inside the 350 pixels to the spectrum streak. On the Vega group stacks the brightest spot away from the star was well under half the star's brightness. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** One star. A target framed more than about 150 pixels off center is not found.
- **To tune for your setup:** If your framing is looser, raise the search window, keeping it short of the distance to the spectrum.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_group_alignment.py`.
- **Raw record:** None kept.

### STK-18

**Finding an extended target (a cluster) in a spectral frame**

- **Claim:** For a broad glow such as a globular cluster, smoothing with a 25-pixel Gaussian and accepting the peak only when no rival within 300 pixels reaches a third of its height finds the cluster reliably, to within a few pixels.
- **Settings it supports:**
  - code constant `EXTENDED_TARGET_MAX_RIVAL_FRACTION` = 1/3 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.EXTENDED_TARGET_MAX_RIVAL_FRACTION --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: confirmed by this entry.
  - code constant `EXTENDED_TARGET_SMOOTHING_SIGMA_PIXELS` = 25 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.EXTENDED_TARGET_SMOOTHING_SIGMA_PIXELS --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: confirmed by this entry.
  - code constant `EXTENDED_TARGET_RIVAL_HALF_WIDTH_PIXELS` = 300 <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.EXTENDED_TARGET_RIVAL_HALF_WIDTH_PIXELS --> in `pipelines/stacking/processing/group_alignment.py` (changing it means editing the code). Relation: confirmed by this entry.
- **Status:** Partly measured. Six frames of one target.
- **Data you need:** Slitless spectral frames of an extended target near the frame center, single frames and a stack.
- **Procedure:**

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.processing.group_alignment import locate_zero_order

  plane = np.asarray(fits.getdata("/path/to/M_13_spectral_frame.fits", memmap=False), dtype=float)
  position = locate_zero_order(plane)
  print(position)
  ```

  `locate_zero_order` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.processing.group_alignment.locate_zero_order --> `measure_spectral_frame_file` reports the same result for a frame as `zero_order_source`. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check.measure_spectral_frame_file -->
- **Acceptance criterion:** The positions across frames agree to about a pixel.
- **What to look at:** `is_extended_target`, the position (row, column) across frames (they should agree to about a pixel), and the rival fraction (rival height over peak height above the sky; near 0 is one clear glow).
- **Our result:** On the six M 13 spectroscopy frames (five single frames and the stack), the point-source search returned nothing because its rival reached 0.61 to 0.64 of the peak (limit 0.5). The extended search found the cluster on all six, with a rival fraction of 0.08. The positions were within about 1 pixel of each other, 3 to 4 pixels from the fixed cluster-core point (row 1493, column 1485) used by the golden tests, mostly along the columns. Other peaks of similar height lie 380 pixels and farther from the center.
- **Verdict:** Not judged.
- **Limits:** One target; the one-third limit, the 25-pixel smoothing and the 300-pixel window were chosen from these frames alone.
- **To tune for your setup:** For a cluster with bright neighbors, measure the rival fraction on your frames and keep the limit above it.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_group_alignment.py` (the golden M 13 point).
- **Raw record:** None kept.

### STK-19

**Measuring the brightness scale between exposure groups on stars**

- **Claim:** The median flux ratio of isolated, unclipped stars measures the scale between two group stacks correctly; the median pixel ratio is pulled by sky gradients and star-width differences. Dropping stars below 10 times their ratio noise removes a bias in the faintest quarter.
- **Settings it supports:**
  - code constant `STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE` = 10 <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE --> in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `STAR_GAIN_APERTURE_RADIUS_PIXELS` = 14 <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.STAR_GAIN_APERTURE_RADIUS_PIXELS --> in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `STAR_GAIN_MINIMUM_STARS` = 16 <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.STAR_GAIN_MINIMUM_STARS --> in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `STAR_GAIN_CLIP_FRACTION_OF_CEILING` = 0.5 <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.STAR_GAIN_CLIP_FRACTION_OF_CEILING --> in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Measured. On three Nikon D5300 targets.
- **Data you need:** An imaging target stacked in two or more exposure groups, with a few hundred stars per frame.
- **Procedure:**
  1. Stack the target and read each group's scale factor, its faint- and bright-quarter ratios and its method.
  2. To compare with the pixel method, call it on the same counts-per-second group images.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.stack("NGC 6888", force=True)
  for group in result.quality_summary.stacking_metrics.exposure_groups:
      print(group.exposure_seconds, group.gain_mid_range, group.gain_bright_end_ratio, group.gain_disagreement)
  ```

  The two methods are `measure_group_gains_with_stars` <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_gains_with_stars --> and `measure_group_gains` <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_gains -->, both internal functions, not part of the public API; may change. `measure_star_flux_ratios` gives the per-quarter ratios. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_star_flux_ratios -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** `gain_mid_range` (the scale factor, a ratio of two counts-per-second values; no units) from each method, and the ratio of the faintest quarter. A scale factor close to the sky ratio rather than to the star ratio means the pixel method is measuring sky, not stars.
- **Our result:** NGC 6888 60 s: pixel method 1.39, star fluxes 1.04. M 51 60 s: pixel method 1.007, equal to its sky ratio (1.00), while its stars read 2.15. M 106, without the signal-to-noise cut: the faintest quarter of 1,600 stars read 1.62 against 1.137 overall. On the D5300 stacks the 10-noise floor of the pixel method sits at 5% to 8% of the sky level, so the source pixels are mostly sky gradients. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** One color camera. Spectral stacks keep the pixel method by design.
- **To tune for your setup:** If too few stars pass on sparse fields, lower `STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE` and watch the faint-quarter ratio.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_exposure_group_star_gains.py`.
- **Raw record:** None kept.

### STK-20

**The linearity tolerance between exposure groups**

- **Claim:** On good group pairs, the faint- and bright-quarter star ratios sit within 4.5% of the group scale factor, so a tolerance of 5% passes them and catches a group that one scale factor cannot describe.
- **Settings it supports:**
  - config key `[Processing.Siril] exposure_group_gain_tolerance` (default 0.05). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.DEFAULT_GAIN_DISAGREEMENT_TOLERANCE -->
- **Status:** Partly measured. The good-pair spread and one failing group are measured; the 5% value is a design choice inside that range.
- **Data you need:** Imaging targets stacked in two or more exposure groups.
- **Procedure:** Stack each target (STK-19) and collect `gain_disagreement` for every non-reference group. `report_gate_limits` reads the same values from every saved stack.

  ```bash
  python -m astrometricslib.scripts.report_gate_limits
  ```

  <!-- impl: astrometricslib.scripts.report_gate_limits -->
- **Acceptance criterion:** Good groups sit well below the tolerance. A group above it is left out and the `exposure_group_linearity` gate fails.
- **What to look at:** `gain_disagreement`: `max(|faint-quarter ratio − scale|, |bright-quarter ratio − scale|) / scale` (a fraction; 0.05 is 5%). Good groups sit well below the tolerance; a group above it is left out and the `exposure_group_linearity` gate fails.
- **Our result:** On the real stacks, the worst quarter of good pairs sat 0.7% to 4.5% from the scale factor, and M 51's 60 s group sat 8% from it. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** A handful of targets on one color camera.
- **To tune for your setup:** If good groups of your camera are left out, raise the tolerance a little above their largest disagreement.
- **Regenerate with:** `astrometricslib/scripts/report_gate_limits.py` (`python -m astrometricslib.scripts.report_gate_limits`).
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_exposure_group_linearity.py`.
- **Raw record:** None kept.

### STK-21

**Leaving out a group with a flooded sky or a failed scale**

- **Claim:** Good groups have a sky brightness per second within a factor of 0.5 to 2 of the reference group; a group with a flooded or foreign sky reads 3.9 or more. A scale factor outside 0.25 to 4 is a failed measurement, not a real scale.
- **Settings it supports:**
  - code constant `SKY_RATIO_LIMIT` = 3.0 <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.SKY_RATIO_LIMIT --> in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `PLAUSIBLE_GAIN_RANGE` = (0.25, 4.0) <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.PLAUSIBLE_GAIN_RANGE --> in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Partly measured. Two bad groups set the sky limit; the scale range is a judgment placed far from two failed measurements.
- **Data you need:** Imaging targets stacked in exposure groups, including one group shot into twilight or moonlight.
- **Procedure:** Stack each target and read each group's sky ratio and left-out reason.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  summary = astro.processing.stack_summary("M 51")
  for group in summary.exposure_groups:
      print(group.get("exposureSeconds"), group.get("gainMidRange"), group.get("leftOutReason"))
  ```

  `ProcessingPipelines.stack_summary` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.stack_summary --> reads what the stage saved. The sky ratio itself is on the internal `GroupGainMeasurement.sky_ratio` <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GroupGainMeasurement --> (not part of the public API; may change) and is quoted in the left-out reason.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The sky ratio (group sky per second over the reference's; no units) and the scale factor of each group.
- **Our result:** Good Nikon groups: sky ratio 0.5 to 2.0. Groups with a flooded or foreign sky: 3.9 and 7.6. On Albireo, two short groups clipped at zero measured scale factors of 1.4e-18 and 6e-31. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Two bad groups for the sky limit.
- **To tune for your setup:** Place `SKY_RATIO_LIMIT` between your good groups' largest ratio and your bad groups' smallest.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_exposure_groups.py` and `test_exposure_groups_combined.py`.
- **Raw record:** None kept.

### STK-22

**Raw frames clipped at zero**

- **Claim:** When the camera offset is below the read noise, a share of raw pixels reads exactly zero and the measured frame noise reads too low. A zero share of 20% separates clipped groups from unclipped ones.
- **Settings it supports:**
  - code constant `CLIPPED_FRAME_ZERO_FRACTION` = 0.2 in `pipelines/stacking/processing/exposure_groups.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.CLIPPED_FRAME_ZERO_FRACTION -->
- **Status:** Partly measured. One session on one camera and offset.
- **Data you need:** A session at several short exposure lengths at low gain and offset, such as a bright-star spectral bracket.
- **Procedure:** Measure the noise and zero share of a few raw frames per exposure length.

  ```python
  from astrometricslib.pipelines.stacking.processing.exposure_groups import measure_frame_noise

  for path in ["/path/to/Vega_0.5s_001.fits", "/path/to/Vega_1s_001.fits", "/path/to/Vega_2s_001.fits"]:
      print(path, measure_frame_noise(path))
  ```

  `measure_frame_noise` is an internal function, not part of the public API; may change. It returns the frame noise in counts and the zero share. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_frame_noise --> After a stack, the `raw_frame_clipping` gate reports the worst group's share.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The zero share (0 to 1) and the noise in counts per exposure length. Noise that rises with exposure until it levels off marks the clipped lengths.
- **Our result:** Vega session (ZWO ASI533MM Pro, gain 0): zero share 71% at 0.5 s, 31% at 1 s, 1% at 2 s, 0.2% at 3 s, 0.02% at 5 s; measured noise 0, 4.2 and 8.4 counts (2, 3 and 5 s all gave 8.4). 0.2 sits between the 1 s group (clipped) and the 2 s group (not).
- **Verdict:** Not judged.
- **Limits:** One camera, one offset.
- **To tune for your setup:** Measure your shortest exposures at your usual gain and offset; place the limit between the clipped and unclipped lengths. Better still, raise the camera offset until no length clips.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_exposure_groups.py`.
- **Raw record:** None kept.

### STK-23

**Trimming the noisy edges of a stack**

- **Claim:** Edges covered by fewer frames are noisier. A noise ratio of 1.15 against the interior separates noisy edges from clean ones, and 25-pixel strips place the edge to within a few dozen pixels.
- **Settings it supports:**
  - code constant `MAXIMUM_EDGE_NOISE_RATIO` = 1.15 <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_crop.MAXIMUM_EDGE_NOISE_RATIO --> in `pipelines/stacking/post_processing/stack_crop.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `STRIP_PIXELS` = 25 <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_crop.STRIP_PIXELS --> in `pipelines/stacking/post_processing/stack_crop.py` (changing it means editing the code). Relation: confirmed by this entry.
  - code constant `MAXIMUM_TRIM_FRACTION` = 0.25 <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_crop.MAXIMUM_TRIM_FRACTION --> in `pipelines/stacking/post_processing/stack_crop.py` (changing it means editing the code). Relation: sanity check only.
  - on/off: config key `[Processing.Siril] trim_noisy_stack_edges_enabled` (default `true`). Relation: sanity check only.
- **Status:** Partly measured. One stack with noisy edges and three with clean edges.
- **Data you need:** A stack of several nights with large shifts between them, and a stack of one night with small dithers. Turn the trim off for these stacks so you see the whole frame.
- **Procedure:**
  1. Set `trim_noisy_stack_edges_enabled = false` and restack both targets.
  2. Measure the noise of 25-pixel strips from each edge as a fraction of the interior's, and ask the crop rule what it would keep.

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.post_processing.stack_crop import find_edge_crop_box

  image = np.asarray(fits.getdata("/path/to/M_27_Stacked.fits", memmap=False), dtype=float)
  plane = image if image.ndim == 2 else image.mean(axis=0)


  def strip_noise(strip):
      """Return the neighbour-difference noise of a strip over its median."""
      differences = np.diff(strip, axis=1).ravel()
      spread = 1.4826 * np.median(np.abs(differences - np.median(differences))) / np.sqrt(2)
      return spread / np.median(strip)


  rows = slice(plane.shape[0] // 5, 4 * plane.shape[0] // 5)
  interior = strip_noise(plane[rows, plane.shape[1] // 2 - 12 : plane.shape[1] // 2 + 13])
  for start in range(0, 200, 25):
      print(start, strip_noise(plane[rows, start : start + 25]) / interior)
  box = find_edge_crop_box(image)
  print(box, box.lost_fraction(plane.shape[1], plane.shape[0]))
  ```

  `find_edge_crop_box` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_crop.find_edge_crop_box --> The rule leaves stars out of its own noise estimate; the short function above does not, so use a starless region or expect a slightly higher ratio.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The edge-to-interior noise ratio per strip (1.0 means as clean as the middle). For frames of equal weight, 1.15 means an edge covered by about three quarters of the frames, since noise goes as one over the square root of the frame count.
- **Our result:** On the 82-frame M 27 stack (three nights), the first 100 columns and 400 rows had 18% to 33% more noise than the interior (ratios 1.18 to 1.33). The Bubble Nebula, M 81 and M 13 stacks had edge ratios of at most 1.06 and are not trimmed, although their frame shifts would have cut 14% to 36% of the area by coverage alone. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Four stacks, one camera.
- **To tune for your setup:** If clean edges are cut, raise `MAXIMUM_EDGE_NOISE_RATIO`; if a ragged border remains, lower it. Re-run the measurement on an untrimmed stack.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_stack_crop.py`.
- **Raw record:** None kept.

### STK-24

**The rejected-pixel limit of 15%**

- **Claim:** On normal sessions the stacker rejects well under 10% of pixel values, so a flag at 15% does not fire on a normal stack. A high rejected share is a weaker signal of trouble than star width.
- **Settings it supports:**
  - code constant `DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD` = 0.15 in `pipelines/stacking/post_processing/stack_quality.py` (changing it means editing the code); false-positive side: normal sessions stay below the limit. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD -->
  - the same constant, detection side: whether the limit catches a session with a genuinely high rejected share (none was available). Relation: sanity check only.
- **Status:** Partly measured. The normal range is measured on three sessions; no session with a genuinely high rejected share was available, so the detection side is not measured.
- **Data you need:** Several stacks made with the rejection map on (`generate_rejmap`, default on), including one partly clouded session.
- **Procedure:** Read the rejected share of each stack, or run `report_gate_limits` across the library.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  quality = astro.quality.stack_quality("NGC 2403", include=["rejected_fraction"])
  print(quality.rejected_fraction)
  ```

- **Acceptance criterion:** The limit sits above every normal session.
- **What to look at:** The rejected fraction (mean share of frames rejected per pixel, 0 to 1). The limit should sit above every normal session.
- **Our result:** Three real sessions (from the rejection sweep of STK-09) never passed about 10%, even NGC 2403's cloud-affected 70-frame session at the loosest limits tried. In the earlier campaign (the first validation campaign, [index.md](index.md#6-validation-sessions) Section 6, before the current rejection limits) M 81 (DS-M81) rejected 0.42% and NGC 2403 (DS-NGC2403) 0.85%; NGC 2403's problem showed in star width (STK-25), not in this share.
- **Verdict:** Not judged.
- **Limits:** Three sessions; the raw sweep tables are not kept.
- **To tune for your setup:** Run `report_gate_limits` on your library and keep the limit above your normal sessions.
- **Regenerate with:** `astrometricslib/scripts/rejection_threshold_analysis.py`, `astrometricslib/scripts/report_gate_limits.py` and `astrometricslib/scripts/run_empirical_validation.py` (`python -m astrometricslib.scripts.run_empirical_validation [--camera NAME]` prints the saved stack summaries of NGC 2403, M 81 and M 13).
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_stack_gates.py`.
- **Raw record:** None kept.

### STK-25

**The sharpness limit: a stack at most 1.2 times wider than expected**

- **Claim:** A stack's expected star width is the root mean square of a sample of its input frames' widths. Normal stacks measure 0.77 to 1.16 times that, so a limit of 1.2 does not fire on them.
- **Settings it supports:**
  - code constant `DEFAULT_FWHM_DEGRADATION_RATIO` = 1.2 in `pipelines/stacking/post_processing/stack_quality.py` (changing it means editing the code); false-positive side: normal stacks stay below the limit. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_FWHM_DEGRADATION_RATIO -->
  - the same constant, detection side: whether the limit catches a real failed alignment (the library holds no such session). Relation: sanity check only.
  - the prediction `expected_stack_fwhm`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.expected_stack_fwhm -->
- **Status:** Partly measured. The false-positive side is measured on six stacks; whether 1.2 catches a real failed alignment is untested, because the library holds no such session.
- **Data you need:** Several imaging stacks, ideally including one made from several nights of different seeing.
- **Procedure:** Stack each target and read the stacked, median-input and expected widths.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.stack("M 27", force=True)
  metrics = result.quality_summary.stacking_metrics
  print(
      metrics.stacked_fwhm_px,
      metrics.median_input_fwhm_px,
      metrics.expected_stack_fwhm_px,
      metrics.fwhm_degraded,
  )
  print(metrics.stacked_fwhm_px / metrics.expected_stack_fwhm_px)
  ```

- **Acceptance criterion:** A well-aligned stack is near 1. A ratio above 1.2 fails `stack_sharpness`.
- **What to look at:** The ratio of stacked to expected width (no units; near 1 is a well-aligned stack, above 1.2 fails `stack_sharpness`). Also the ratio against the median input width, to see why the root mean square is used.
- **Our result:** Six ZWO ASI533MM Pro stacks on 2026-10-03: Bubble Nebula 1.10, M 27 1.16, M 81 1.06, M 13 1.01, M 101 0.85, NGC 2244 0.77. Against the median of the inputs the same stacks reached up to 1.34 (M 81). In a combined exposure-group stack, stars whose cores came from a shorter group read too wide: on the M 57 stack of 2026-10-03 they read 3.40 px against 2.38 px for the other stars, which is why they are left out of the measurement. In the earlier campaign (median-based check, the first validation campaign in [index.md](index.md#6-validation-sessions) Section 6), M 81 (DS-M81) gave 1.03 and NGC 2403 (DS-NGC2403) 1.20 (rounded; flagged) with only 0.85% of pixels rejected. NGC 2403 has not been re-measured with the current check.
- **Verdict:** Not judged.
- **Limits:** No real failed-registration session; six stacks from one camera.
- **To tune for your setup:** Read the ratio on your normal stacks; keep the limit a little above the largest.
- **Regenerate with:** `astrometricslib/scripts/run_empirical_validation.py` (the first validation campaign) and `astrometricslib/scripts/report_gate_limits.py`.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_fwhm_degradation_sampling.py` and `test_combined_stack_fwhm.py`.
- **Raw record:** None kept.

### STK-26

**Blank and over-subtracted stacks: the zero-pixel and negative-pixel limits**

- **Claim:** A stack with more subtracted than the sky it held is mostly exact zeros; normal imaging stacks have at most 39% zero pixels (a bright star on a dark sky) and usually 6% or fewer. Clean spectral stacks have at most 0.4%; the known bad ones 32% or more.
- **Settings it supports:**
  - code constant `DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD` = 0.5 <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD --> in `pipelines/stacking/post_processing/stack_quality.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD` = 0.05 <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD --> in `pipelines/stacking/post_processing/stack_quality.py` (changing it means editing the code). Relation: derived from this entry.
  - code constant `DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT` = 50 <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT --> in `pipelines/stacking/post_processing/stack_quality.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Measured. Library-wide surveys of stored stacks.
- **Data you need:** All your imaging and spectral stacks.
- **Procedure:** List the zero share of every stack. `rebuild_stacks` reads every stack in the library without changing anything and names the blank ones; `report_gate_limits` reports the stored values.

  ```bash
  python -m astrometricslib.scripts.rebuild_stacks
  python -m astrometricslib.scripts.report_gate_limits
  ```

  <!-- impl: astrometricslib.scripts.rebuild_stacks -->
- **Acceptance criterion:** The limit sits in the gap between clean and broken stacks.
- **What to look at:** The zero-pixel fraction (share of pixels exactly zero, the worst channel for a color stack; 0 to 1). The limit should sit in the gap between clean and broken stacks.
- **Our result:** Imaging (2026-09-21): 15 of the 59 library stacks, nearly all from the Nikon D5300, were 98% to 100% zero, because Siril subtracted the bias again from a dark that already held it. No other imaging stack had more than 39% (Vega L), and the rest 6% or fewer. Siril's "many negative pixels" warning read 99% and 100% in those runs and never appeared on the ASI533MM Pro. Spectral (17 stacks, 2026-10-12): 0% for 13 stacks, 0.28% Deneb, 0.4% Arcturus, 32% Sirius (the matched dark removed the sky), 35% Alnath (every group clipped at zero). Vega and Albireo read 0% although their short raw frames are clipped, so this limit does not detect clipping (STK-22 does).
- **Verdict:** Not judged.
- **Limits:** Two cameras.
- **To tune for your setup:** Run the survey and place the limits between your clean and broken stacks.
- **Regenerate with:** `astrometricslib/scripts/rebuild_stacks.py` (`python -m astrometricslib.scripts.rebuild_stacks [--target NAME --apply --backup-dir DIR]`; without `--apply` it only reads).
- **Guarded by tests:** `astrometricslib/scripts/test/test_rebuild_stacks.py` and `astrometricslib/pipelines/stacking/test/test_stack_gates.py`.
- **Raw record:** None kept.

### STK-27

**Star-based color calibration compared with Siril's PCC and SPCC**

- **Claim:** The star-based color gains agree with Siril's photometric (PCC) and spectrophotometric (SPCC) color calibration in direction and rough size, and the three methods differ from each other by 10% to 20%. The star-based gains are close to SPCC on galaxy fields and give too much blue on Milky Way fields.
- **Settings it supports:**
  - config key `[Processing.Siril] preview_colour_calibration_method` (default `pcc`; `stars` uses the star-based method alone). Relation: sanity check only.
  - code constant `BRIGHTNESS_PERCENTILE_RANGE` = (30, 85) <!-- impl: astrometricslib.pipelines.stacking.post_processing.colour_calibration.BRIGHTNESS_PERCENTILE_RANGE --> in `pipelines/stacking/post_processing/colour_calibration.py`. Relation: sanity check only.
  - code constant `MINIMUM_STARS` = 40 <!-- impl: astrometricslib.pipelines.stacking.post_processing.colour_calibration.MINIMUM_STARS --> in `pipelines/stacking/post_processing/colour_calibration.py`. Relation: sanity check only.
- **Status:** Partly measured. The gains of the three methods are measured on six stacks; which one is right is not known, and the default was chosen by eye.
- **Data you need:** Color (Bayer) stacks of galaxy fields and of Milky Way fields, after gradient removal. Siril 1.4 or later with a network connection for PCC and SPCC.
- **Procedure:**
  1. Measure the star-based gains on the linear, gradient-flattened stack.
  2. Run `pcc` and `spcc` in Siril on the same data and divide Siril's red and blue factors by its green.

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.post_processing.colour_calibration import measure_gains

  image = np.asarray(fits.getdata("/path/to/M_51_graxpert.fits", memmap=False), dtype=float)  # shape (3, h, w)
  calibration = measure_gains(image)
  print(calibration.red_gain, calibration.blue_gain, calibration.star_count, calibration.red_uncertainty)
  ```

  `measure_gains` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.post_processing.colour_calibration.measure_gains -->
- **Acceptance criterion:** The methods agree within a few percent on galaxy fields.
- **What to look at:** The red and blue gains with green fixed at 1 (no units). Agreement between methods within a few percent on galaxy fields; the size of the disagreement on dusty fields.
- **Our result:** Siril 1.4.4; SPCC with the "Nikon D5300" sensor and the "Average Spiral Galaxy" white reference. Red / blue gains:

  | Target | Star-based | PCC | SPCC |
  | --- | --- | --- | --- |
  | M 51 | 1.48 / 1.34 | 1.79 / 1.10 | 1.68 / 1.29 |
  | M 31 | 1.78 / 1.33 | 1.85 / 1.29 | 1.60 / 1.29 |
  | M 106 | 1.69 / 1.33 | 1.93 / 1.24 | 1.72 / 1.29 |
  | NGC 6992 | 1.42 / 1.56 | 1.58 / 1.61 | 1.61 / 1.41 |
  | IC 1805 | 1.51 / 1.66 | 1.62 / 1.74 | 1.78 / 1.41 |
  | NGC 6888 | 1.60 / 1.51 | 1.84 / 1.52 | 1.86 / 1.30 |

  Every method raises red by 1.4 to 1.9 and blue by 1.1 to 1.7. On M 51, M 31 and M 106 the star-based blue gain is within 3% of SPCC; on the three Milky Way fields it is 11% to 18% higher, and its red gain is 2% to 15% below SPCC in five of six fields. The star-based method's printed uncertainty (0.1% to 1.2%) is the scatter between stars only. PCC took about 10 to 13 seconds per stack (13 s on M 51) and SPCC about 45 seconds; the star-based method a few seconds, offline. With a shared stretch the pictures differ little; PCC gives the warmest galaxy core. With Siril's default per-channel stretch, the calibrated and uncalibrated pictures of M 51 came out identical. The user chose PCC as the default after seeing the pictures. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** One camera; the white reference decides much of the PCC-SPCC difference. The cause of the Milky Way bias (dust reddening the median star) is not tested.
- **To tune for your setup:** Choose the method by comparing pictures of your own targets; if you use `stars` on dusty fields, expect too much blue.
- **Regenerate with:** No script; measured by hand with Siril.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_colour_calibration.py`.
- **Raw record:** None kept.

### STK-28

**Green removal on color stacks**

- **Claim:** Uncalibrated color stacks have a strongly green sky, and Siril's Remove Green Noise changes little once the color calibration has run.
- **Settings it supports:**
  - config key `[Processing.Siril] preview_remove_green_noise_enabled` (default `true`). Relation: sanity check only.
- **Status:** Partly measured. Four stacks for the green cast and two for the effect.
- **Data you need:** Color stacks of a few targets.
- **Procedure:** Remake the preview with and without green removal (change the setting between runs) and compare the sky's mean red, green and blue values in the two JPEGs.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.remake_preview("M 51")
  print(result.preview_path, result.steps_run, result.previous_pictures)
  ```

  `ProcessingPipelines.remake_preview` keeps the old pictures in `_previous_preview` for the comparison. <!-- impl: astrometricslib.api.processing.ProcessingPipelines.remake_preview -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The sky's green-to-red ratio before calibration, and the change in the sky's green level (0 to 255) between the two pictures.
- **Our result:** The sky of four Nikon stacks had 1.5 to 2 times as much green as red. With the color calibration on, green removal moved the sky's green on M 51 and M 31 by about 2 levels out of 255. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** Few stacks; picture-level check only.
- **To tune for your setup:** Turn it off if it does nothing visible on your stacks.
- **Regenerate with:** No script; measured by hand.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_stack_preview.py`.
- **Raw record:** None kept.

### STK-29

**The sky level of the preview stretch**

- **Claim:** Placing the brightest extended light at a fixed display brightness gives faint targets a lighter sky and bright targets a darker one, in the right order across many stacks.
- **Settings it supports:**
  - code constant `EXTENDED_LIGHT_BRIGHTNESS` = 0.36 <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_level.EXTENDED_LIGHT_BRIGHTNESS --> in `pipelines/stacking/post_processing/sky_level.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `LIGHTEST_SKY_LEVEL` = 0.20 <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_level.LIGHTEST_SKY_LEVEL --> in `pipelines/stacking/post_processing/sky_level.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `DARKEST_SKY_LEVEL` = 0.11 <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_level.DARKEST_SKY_LEVEL --> in `pipelines/stacking/post_processing/sky_level.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `FALLBACK_SKY_LEVEL` = 0.18 <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_level.FALLBACK_SKY_LEVEL --> in `pipelines/stacking/post_processing/sky_level.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Partly measured. The tuned number was set by eye on one stack and checked for order on 22 others.
- **Data you need:** Stacks of faint and bright targets from your camera.
- **Procedure:**

  ```python
  from astrometricslib.pipelines.stacking.post_processing.sky_level import choose_sky_level_for_file

  for path in ["/path/to/M_101_Stacked.fits", "/path/to/M_42_Stacked.fits"]:
      choice = choose_sky_level_for_file(path)
      print(path, choice.sky_level, choice.extended_light_snr, choice.reason)
  ```

  `choose_sky_level_for_file` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_level.choose_sky_level_for_file -->
- **Acceptance criterion:** Fainter targets get lighter skies; then the pictures are judged by eye.
- **What to look at:** The chosen sky level (0 to 1 of full brightness) against the extended light's signal-to-noise ratio. Fainter targets should get lighter skies; then judge the pictures by eye.
- **Our result:** ASI533MM Pro stacks: 0.20 for M 101, M 81, M 27 and the faint star fields, about 0.19 for the Bubble Nebula, 0.11 for M 42. The tuned number was set by eye on the Bubble Nebula stack, where the user found a sky of 0.15 too black and chose a level near 0.20 from rendered options. On M 27 a sky of 0.234 looked grainier than 0.19; 0.18 was judged best on M 81; a sky of 0.11 gave M 42 the strongest contrast while M 101's faint arms nearly vanished. Checked on 22 other stacks, where the levels stayed in order. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Judged by eye; one camera.
- **To tune for your setup:** Raise `EXTENDED_LIGHT_BRIGHTNESS` for darker skies overall, lower it for lighter; adjust the two limits if your stacks are grainier or cleaner.
- **Regenerate with:** No script; judged by eye.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_sky_level.py`.
- **Raw record:** None kept.

### STK-30

**Denoising after the stretch, and its strength**

- **Claim:** Cosmic Clarity's denoise removes far more visible grain when it runs after the stretch than before it, and a strength of 0.9 removes more grain than 0.75 without changing star brightness.
- **Settings it supports:**
  - config key `[Processing.CosmicClarity] denoise_strength` (default 0.9). Relation: confirmed by this entry. <!-- impl: astrometricslib.foundation.config.AppConfiguration.get_cosmic_clarity_denoise_strength -->
  - the order of the preview steps in `write_stack_preview`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_preview.write_stack_preview -->
- **Status:** Partly measured. One stack for the order, four for the strength.
- **Data you need:** Stacks of faint targets with visible sky grain, and Cosmic Clarity configured.
- **Procedure:** Remake the preview at two strengths and compare the grain of the sky.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for strength in (0.75, 0.9):
      result = astro.processing.remake_preview("M 101", denoise_strength=strength)
      print(strength, result.preview_path, result.steps_run)
  ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The sky grain: the standard deviation of a starless sky patch in the stretched picture, relative to a picture without denoise (a 73% fall means 73% less grain). Star peak values should not change.
- **Our result:** On the M 13 stack, visible grain fell by 22% with the denoise before the stretch and by 73% after it. On M 101, M 57, NGC 4438 and M 81, strength 0.9 cut the sky grain by 14% to 39% compared with 0.75 and left star peaks unchanged. On M 13, 0.5 left visible grain. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** The grain measurement method is not recorded.
- **To tune for your setup:** Lower the strength if smooth areas look plastic; raise it if grain remains.
- **Regenerate with:** No script; measured by hand.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_stack_preview.py`.
- **Raw record:** None kept.

### STK-31

**The StarNet star-separated stretch**

- **Claim:** Stretching the stars on their own with an arcsinh curve of strength 1000 and blending them back at 0.8 gives rounder, more natural stars than the star toning, at the cost of half a minute per preview.
- **Settings it supports:**
  - code constant `DEFAULT_STAR_STRETCH` = 1000 <!-- impl: astrometricslib.pipelines.stacking.post_processing.starnet_stretch.DEFAULT_STAR_STRETCH --> in `pipelines/stacking/post_processing/starnet_stretch.py`. Relation: sanity check only.
  - code constant `DEFAULT_STAR_STRENGTH` = 0.8 <!-- impl: astrometricslib.pipelines.stacking.post_processing.starnet_stretch.DEFAULT_STAR_STRENGTH --> in `pipelines/stacking/post_processing/starnet_stretch.py`. Relation: sanity check only.
  - per-run arguments `star_stretch` and `star_strength` of `ProcessingPipelines.remake_preview`. Relation: sanity check only.
- **Status:** Partly measured. Run time is measured; the defaults were chosen by eye on three stacks.
- **Data you need:** Stacks of a galaxy, a nebula and a star field; StarNet set up in Siril.
- **Procedure:**

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for stretch in (300.0, 1000.0, 3000.0):
      result = astro.processing.remake_preview("M 51", starnet=True, star_stretch=stretch, star_strength=0.8)
      print(stretch, result.preview_path, result.steps_run)
  ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** By eye: how many faint stars show, and whether they look soft. The job log gives the time each step took.
- **Our result:** On M 81, M 51 and NGC 6992, StarNet added 33 to 56 seconds to a preview and gave rounder, more natural stars and a slightly cleaner galaxy than the star toning, with the sky at the same level. On M 51, a stretch of 300 showed few faint stars and 3000 made them soft; a strength of 0.6 left stars dim and 1.0 crowded the nebula. StarNet was poor on the core of M 13. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Three stacks, judged by eye.
- **To tune for your setup:** Pass `star_stretch` and `star_strength` per run until the pictures look right, then change the defaults in the code.
- **Regenerate with:** No script; judged by eye.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_starnet_stretch.py`.
- **Raw record:** None kept.

### STK-32

**Star toning and its noise-aware start**

- **Claim:** Dimming star light by a power of 1.8 restores some of the stars' brightness range. Counting light as a star only when it is at least 6 sky-noise widths above the local background stops the toning from dimming the sky itself.
- **Settings it supports:**
  - code constant `STAR_DIMMING_POWER` = 1.8 <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.STAR_DIMMING_POWER --> in `pipelines/stacking/post_processing/star_tone.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `HIGHLIGHT_KNEE` = 0.65 <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.HIGHLIGHT_KNEE --> in `pipelines/stacking/post_processing/star_tone.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `STAR_DIMMING_STARTS_AT` = 0.05 <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.STAR_DIMMING_STARTS_AT --> in `pipelines/stacking/post_processing/star_tone.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `STAR_DIMMING_FULL_AT` = 0.12 <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.STAR_DIMMING_FULL_AT --> in `pipelines/stacking/post_processing/star_tone.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `NOISE_WIDTHS_ABOVE_THE_BACKGROUND` = 6.0 <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.NOISE_WIDTHS_ABOVE_THE_BACKGROUND --> in `pipelines/stacking/post_processing/star_tone.py` (changing it means editing the code). Relation: derived from this entry.
  - on/off: config key `[Processing.Siril] preview_star_tone_enabled` (default `true`). Relation: sanity check only.
- **Status:** Partly measured. The noise-aware start is measured on four stacks; the power, the knee and the two fixed limits were chosen by eye.
- **Data you need:** Stretched pictures (the `_processed.fits` beside a stack) of galaxies with spiral-arm knots and of a star field.
- **Procedure:**

  ```python
  import numpy as np
  from astropy.io import fits

  from astrometricslib.pipelines.stacking.post_processing.star_tone import estimate_noise, tone_stars

  picture = np.asarray(fits.getdata("/path/to/M_51_Stacked_processed.fits", memmap=False), dtype=float)
  plane = picture if picture.ndim == 2 else picture.mean(axis=0)
  toned = tone_stars(picture)
  toned_plane = toned if toned.ndim == 2 else toned.mean(axis=0)
  print(estimate_noise(plane), np.median(plane), np.median(toned_plane))
  ```

  `tone_stars` <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.tone_stars --> and `estimate_noise` <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_tone.estimate_noise --> are internal functions, not part of the public API; may change.
- **Acceptance criterion:** The sky level (median of the picture) does not change when the toning is applied.
- **What to look at:** The sky level (median of the picture) before and after toning: it should not change. The noise in stretched units (0 to 1): if it is above the 0.05 start, the fixed limit alone would treat the sky as stars.
- **Our result:** On M 51, M 106, M 81 and M 101, the sky noise was about 0.055 in stretched units, and with the fixed 0.05 start about 90% of sky pixels counted as star light. The toning halved the sky level (0.20 to about 0.10) and smeared the arms of M 81. With the 6-width start (about 0.26 to 0.34 in stretched units on these stacks) the sky level stays where the sky-level rule put it, and bright stars are still toned. The background under the stars lies 2 to 3 noise widths below the sky, so sky noise alone reaches 5.5 widths above it. The power 1.8 (against 1.4) and the knee 0.65 (against 0.5) were picked by the user on the Bubble Nebula. The 0.05 and 0.12 limits were set on the Bubble Nebula, M 81, M 45 and NGC 2244 (M 81's arm knots reach only 0.02 to 0.08 above the background). Toning each color channel alone turned the sky of M 51 yellow in the middle and blue at the edges, which is why a color picture is toned on its brightness. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Judged by eye except for the sky-level check.
- **To tune for your setup:** If faint structure is flattened, raise `STAR_DIMMING_STARTS_AT`; if the sky darkens, raise `NOISE_WIDTHS_ABOVE_THE_BACKGROUND`.
- **Regenerate with:** No script; measured by hand.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_star_tone.py`.
- **Raw record:** None kept.

### STK-33

**Recognizing a bright extended object such as the Moon**

- **Claim:** The normal stretch whites out at most 0.12% of a nebula, galaxy or star field but 4.4% of the Moon, so a share of 1% switches to the bright-object stretch.
- **Settings it supports:**
  - code constant `SATURATED_FRACTION_LIMIT` = 0.01 in `pipelines/stacking/post_processing/bright_object.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.bright_object.SATURATED_FRACTION_LIMIT -->
  - `BRIGHT_OBJECT_BRIGHTNESS` = 0.55 <!-- impl: astrometricslib.pipelines.stacking.post_processing.bright_object.BRIGHT_OBJECT_BRIGHTNESS --> and `WHITE_POINT_PERCENTILE` = 99.8 <!-- impl: astrometricslib.pipelines.stacking.post_processing.bright_object.WHITE_POINT_PERCENTILE --> in the same file (chosen by eye). Relation: sanity check only.
- **Status:** Partly measured. 38 stacks for the white share, but the Moon is the only bright object among them.
- **Data you need:** Your stacks of normal targets and of the Moon (or another large bright object).
- **Procedure:**

  ```python
  from astrometricslib.pipelines.stacking.post_processing.bright_object import (
      choose_bright_object_stretch_for_file,
  )

  for path in ["/path/to/Moon_Stacked.fits", "/path/to/M_45_Stacked.fits"]:
      stretch = choose_bright_object_stretch_for_file(path)
      print(path, None if stretch is None else stretch.white_fraction)
  ```

  `choose_bright_object_stretch_for_file` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.post_processing.bright_object.choose_bright_object_stretch_for_file --> It returns `None` for a normal stack; `white_fraction_after_autostretch` gives the share for any stack. <!-- impl: astrometricslib.pipelines.shared.image_scaling.white_fraction_after_autostretch -->
- **Acceptance criterion:** The limit sits between the brightest normal stack and the bright objects.
- **What to look at:** The share of pixels the normal stretch pushes to white (0 to 1). The limit should sit between the brightest normal stack and the bright objects.
- **Our result:** On 38 stacks the normal stretch whited out 4.4% of the Moon and at most 0.12% (M 45) of every nebula, galaxy and star field. 1% is eight times above the others and four times below the Moon. Date not recorded.
- **Verdict:** Not judged.
- **Limits:** The Moon only. A planet covers well under 0.01% of the frame and is not caught.
- **To tune for your setup:** If a bright nebula core switches route, raise the limit; if a smaller bright object does not, lower it.
- **Regenerate with:** No script; measured by hand.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_bright_object.py`.
- **Raw record:** None kept.

### STK-34

**Raw-frame check: star-count limits and the faint-trail detector**

- **Claim:** On the frames below, a typical frame held several thousand bright regions and the frame exposed during a drift held under a quarter of that, so a low limit of half the batch median separates them. On four M 13 lights the star count stayed within 4% of the batch median, and the one light with a faint trail crossed the high limit only because of the trail. The faint trail stood far above the noise level of the trail detector's threshold.
- **Settings it supports:**
  - code constant `LOW_STAR_FRACTION` = 0.5 in `pipelines/shared/quality/raw_frame_check.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.LOW_STAR_FRACTION -->
  - code constant `HIGH_STAR_MULTIPLE` = 1.4 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.HIGH_STAR_MULTIPLE -->
  - code constants `TRAIL_BLOCK_PIXELS` = 4, `TRAIL_THRESHOLD_SIGMA` = 3.0, `TRAIL_STAR_SIGMA` = 60.0 and `TRAIL_MIN_DIAGONAL_FRACTION` = 0.25 in the same file (the last is also an argument of `check_raw_frames`). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.TRAIL_BLOCK_PIXELS --> <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.TRAIL_THRESHOLD_SIGMA --> <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.TRAIL_STAR_SIGMA --> <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.TRAIL_MIN_DIAGONAL_FRACTION -->
- **Status:** Partly measured. One session for the low limit and one set of four clean lights and one trailed light for the high limit and the trail detector; no limit was fitted to these frames.
- **Data you need:** A batch of raw light frames of one field from one session. Ideally the batch holds a frame exposed during a drift or a cloud, and a frame crossed by a satellite or aircraft trail. A clean batch of the same field gives the normal spread of the star count.
- **Procedure:**
  1. Run the raw-frame check on the batch and read each frame's star count, the longest straight trail and its angle.
  2. Divide each star count by the batch median and compare with the two limits.
  3. For the trail detector, find the frame with the trail and read the peak of the trail's regions in noise units from the star detection step, and the share of blocks in pure noise above the block threshold.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  diagnostics = astro.quality
  check = diagnostics.frame_quality("M 13", kind="raw_check", limit=300)
  for row in check.frames:
      print(row.get("path"), row.get("star_count"), row.get("line_trail_px"), row.get("line_trail_angle_deg"))
  ```

  `QualityDiagnostics.frame_quality` <!-- impl: astrometricslib.api.quality.QualityDiagnostics.frame_quality --> runs `check_raw_frames` (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.check_raw_frames -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Each frame's star count (connected bright regions) divided by the batch median, with no units; the longest straight trail in pixels; and the peak of the trail's regions in noise units (multiples of the noise standard deviation).
- **Our result:** The star counts for the typical and drifting frames come from a session of 2026-10-02 (STK-01 describes a 56-frame target of the same date; that they are the same session is not recorded). The other rows come from M 13 lights whose date is not recorded. Sample sizes beyond those named are not recorded, and no uncertainty was estimated. The result is in-sample for the trail detector and was measured with an earlier version of the check (code version not recorded).

  | Measurement | Value |
  | --- | --- |
  | Bright regions in a typical frame, 2026-10-02 | 3,200 to 3,550 |
  | Bright regions in the frame exposed during a 2 min drift | 783 (under a quarter of typical) |
  | Bright regions in four clean M 13 lights | 602 to 635 (within 4% of the median) |
  | Bright regions in the M 13 light with a faint trail | 934 against a median of 624 (1.50 times) |
  | Peak of the faint trail after the smoothing used for star detection | about 40 noise units |
  | The 353 detection regions on the trail | median peak 42, 90th percentile 50 noise units |
  | Other detection regions | half peaked above about 95 noise units |
  | Share of pure-noise blocks above 3 noise units | 0.13% |
  | Longest straight chains of stars and noise in the clean M 13 lights | 20 to 228 px (376 px on the spectroscopy frame, where the spectrum is a straight streak) |
  | Longest straight trail found in the trailed light | 3,220 px |
  | Run time of the trail detector on a 3008 by 3008 frame | about 0.3 s |

- **Verdict:** Not judged.
- **Limits:** One session and one trailed frame, from one camera. The high limit of 1.4 was designed to mirror the low limit and was not fitted. No false-alarm rate of the trail detector on a large clean set was measured. The 0.13% is the noise share for a Gaussian model, not a count on real blocks.
- **To tune for your setup:** If clean frames are flagged for a high count, raise `HIGH_STAR_MULTIPLE`; if trailed frames pass, lower `TRAIL_MIN_DIAGONAL_FRACTION`. Re-run step 1 on a batch with a known trail after each change.
- **Regenerate with:** No script; measured by hand with the calls above.
- **Guarded by tests:** `astrometricslib/pipelines/shared/quality/test/test_raw_frame_check.py` checks the rules on made-up frames.
- **Raw record:** None kept.

## Design estimates

These tuned values have not been measured. Each is a reasoned choice; the last column says how you could measure it on your data.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| STK-D01 | Fewest frames per master calibration frame | 3 | config key `[Processing.Siril] minimum_calibration_frames` | Noisy master calibration frames add noise to every stack. | Build masters from 1, 3, 5 and 10 frames and compare stack noise with `QualityDiagnostics.stack_quality(path, compare_to=...)`; the minimum is where the gain stops mattering. |
| STK-D02 | Rejection limits for bias and dark masters | `rej 3 3` | Siril script in `drivers/siril_interface.py` | Masters with more noise or leftover hot pixels. | Stack masters at 2.5 and 3.5 and compare their noise and hot-pixel counts. |
| STK-D03 | Fewest frames in an exposure group | 5 | code constant `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` in `pipelines/stacking/processing/exposure_groups.py` | Groups that register poorly or reject too many pixels. | Stack groups of 3, 5 and 8 frames with `ProcessingPipelines.stack(frames=...)` and check the registration share and rejected fraction. |
| STK-D04 | Saturated pixel share that flags a stack | 0.1% | code constant `DEFAULT_SATURATION_FLAG_THRESHOLD` in `pipelines/shared/quality/saturation.py` | Normal stacks flagged as saturated, or saturated stacks missed. | Run `report_gate_limits` across your stacks and see where normal stacks sit. |
| STK-D05 | Flat level limits | 10% and 90% of full scale | code constants `MINIMUM_FLAT_LEVEL_FRACTION`, `MAXIMUM_FLAT_LEVEL_FRACTION` in `pipelines/stacking/pre_processing/flat_calibration.py` | Flats that are too dim or too bright give uneven flatness. | Take flats at 5% to 95% of full scale, stack lights with each, and compare flatness with `QualityDiagnostics.stack_quality(..., compare_to=...)`. |
| STK-D06 | Flat blur width limits | 1 to 8 pixels | code constants `MINIMUM_SMOOTHING_SIGMA_PIXELS`, `MAXIMUM_SMOOTHING_SIGMA_PIXELS` in the same file | Dust shadows blurred away, or noise left in the flat. | Blur a noisy master by several widths and check that dust shadows survive in the stack's flatness measurement. |
| STK-D07 | Dark exposure match | within 0.1 s | `CalibrationLibrary.select_dark_frames` in `drivers/calibration_library.py` | Mismatched darks over- or under-subtract. | Compare masters of nearby exposures with `CalibrationCatalog.query` and the stack's negative-pixel warning. |
| STK-D08 | Noise penalty that switches on weighting | 1.05 | code constant `MINIMUM_PENALTY_TO_WEIGHT` (STK-07) | Weighting switched on when it does not help, or off when it does. | Stack mixed sessions with P between 1.0 and 1.2 both ways and compare the noise. |
| STK-D09 | Spectral registration share before a retry | 0.9 | code constant `MINIMUM_REGISTERED_FRACTION` (STK-12) | Spectral groups retried too often or too late. | Read the per-group frames stacked and submitted on your spectral sessions. |
| STK-D10 | Brightness-scale pixel method | 20th to 80th percentile; brightest 1% | code constants `GAIN_MID_RANGE_PERCENTILES`, `GAIN_BRIGHTEST_FRACTION` in `pipelines/stacking/processing/exposure_groups.py` | A wrong brightness scale between exposure groups. | Compare `measure_group_gains` with `measure_group_gains_with_stars` on imaging groups (STK-19). |
| STK-D11 | Saturation level of a group stack | 0.95 of full scale | code constant `SATURATION_FRACTION_OF_FULL_SCALE` in the same file | Saturated pixels treated as good data, or good pixels masked. | Read the plateau of a saturated star in your group stacks with `estimate_saturation_mask_level`. |
| STK-D12 | Clipping floor for clipped groups | 2 frame-noise widths | code constant `CLIPPING_FLOOR_SIGMAS` in the same file | Clipped groups mis-weighted at faint pixels. | Compare a clipped group with an unclipped one of the same field at faint pixels. |
| STK-D13 | Session gap for the quarantine | 4 hours | code constant `SESSION_GAP_SECONDS` in `pipelines/stacking/pre_processing/frame_quarantine.py` | Frames from one session judged against another session's median. | List the gaps between your frames with `QualityDiagnostics.frame_quality(kind="raw_check")`. |
| STK-D14 | Star-pattern matching constants | 150 stars, 8 neighbors, 1% shape tolerance | code constants in `pipelines/stacking/processing/star_pattern_candidates.py` | Star-pattern matches missed or false between group stacks. | Run `find_alignment_from_star_patterns` on group pairs of your camera and count matches found. |
| STK-D15 | Pre-fetch readers | 4 | config key `[Processing] prefetch_workers` | Slow reads on a network drive. | Time a stack with 1, 2, 4 and 8 readers on your network drive; the job log's `Pre-fetch:` line gives the time. |
| STK-D16 | Siril runs at once | 2 | config key `[Processing.Parallelism] max_concurrent_jobs` | Slower stacking or failed Siril runs. | Run `python -m astrometricslib.scripts.benchmark_siril_concurrency --target A --target B --slots 1 --slots 2` and pick the fastest count with no failures. |
| STK-D17 | Color calibration star finder | 8σ; 3.5 px circle; 7 to 11 px ring | code constants `DETECTION_SIGMA`, `APERTURE_RADIUS_PIXELS`, `ANNULUS_INNER_RADIUS_PIXELS`, `ANNULUS_OUTER_RADIUS_PIXELS` in `pipelines/stacking/post_processing/colour_calibration.py` | Color gains that change by more than their uncertainty. | Run `measure_gains` with each value moved and see whether the gains change by more than their uncertainty. |
| STK-D18 | Flat saturation level | 98% of full scale | code constant `_SATURATED_FRACTION_OF_FULL_SCALE` in `pipelines/stacking/pre_processing/flat_calibration.py` | Saturated flat pixels counted as good, or good ones counted as saturated. | Take flats at rising exposure and note the level where the share of clipped pixels starts to rise. |
| STK-D19 | Raw-frame check: high star count | 1.4 times the batch median | code constant `HIGH_STAR_MULTIPLE` in `pipelines/shared/quality/raw_frame_check.py` | Good frames flagged for a high count, or frames with trails and light leaks missed. | Run the raw check on many clean batches (`QualityDiagnostics.frame_quality(kind="raw_check")`) and set the limit above the largest normal ratio. |
| STK-D20 | Raw-frame check: faint-trail detector | 4 px blocks; 3σ block threshold; 60σ star mask; trail at least 0.25 of the frame diagonal | code constants `TRAIL_BLOCK_PIXELS`, `TRAIL_THRESHOLD_SIGMA`, `TRAIL_STAR_SIGMA`, `TRAIL_MIN_DIAGONAL_FRACTION` in `pipelines/shared/quality/raw_frame_check.py` | Faint trails missed, or star chains and noise flagged as trails. | Add synthetic trails of known brightness to clean frames and count how many `line_trail_px` finds; run it on clean frames to count false trails. |
| STK-D21 | Frame-trend limits | at least 4 frames; at least 70% of steps in one direction | code constants `MINIMUM_FRAMES`, `STEADY_STEP_FRACTION` in `pipelines/shared/quality/frame_trends.py` | Slow drifts missed, or noise reported as a trend. | Run the trend check on sessions with a known drift (dew, focus) and on steady ones, and count false and missed trends. |
