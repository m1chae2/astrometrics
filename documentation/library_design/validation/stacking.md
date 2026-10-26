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
  check = diagnostics.measure_frames(
      "M 27", kind="raw_check", filter_name="L", since="2026-09-24T18:00", until="2026-09-25T06:00", limit=300
  )
  for row in check.frames:
      print(row.get("path"), row.get("star_count"), row.get("roundness"), row.get("sky_median_adu"))
  # Step 2: what the quarantine would move, and why. Nothing is moved.
  preview = diagnostics.measure_frames("M 27", kind="quarantine_preview", limit=300)
  for frame in preview.would_move:
      print(frame.path, frame.reason)
  print(preview.notes)
  ```

  `QualityRecords.measure_frames` <!-- impl: astrometricslib.api.quality.QualityRecords.measure_frames --> runs the same measurement (`measure_raw_frame`, internal function, not part of the public API; may change) <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.measure_raw_frame --> and the same judgment (`judge_batch`, internal function, not part of the public API; may change) <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.judge_batch --> as the stacker.
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
- **Limits:** Two clouded and two trailed frames make a small set of known bad cases. The sessions are all from the ZWO ASI533MM Pro (a monochrome camera). The 1.15 sky multiple only changes the text of the reason. Since 2026-10-24 the `frame.quarantine_roundness` and `frame.quarantine_sky_median_adu` rows of a frame the step moved carry `action_taken` = `frame moved to _excluded/: <kind> (<reason>)` (a processing decision is its measurement plus the action taken). Rows written before this date lack it; for those frames `QualityRecords.set_aside_frames` falls back to the folder's `excluded_frames.json` and says so (`reason_source`: stored_row, manifest, folder_only). The limits are untouched.
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
      preview = astro.quality.measure_frames(target.id, kind="quarantine_preview", limit=300)
      if preview.frames_checked:
          print(target.id, preview.would_move_total, preview.frames_checked, preview.notes)
  ```

- **Acceptance criterion:** The cap is well above the largest share moved in any batch.
- **What to look at:** The largest share moved in any batch, as a fraction. The cap should be well above it. A `notes` line saying a batch was left alone because too many frames would move points to limits that do not suit the equipment.
- **Our result:** A dry run on the library picked 4 of 56 frames (7%) of the 2026-10-02 target, 4 of 30 (13%) and 5 of 40 (12%) of two M 27 sessions, and none in the other six batches. A quarter is about twice the largest share.
- **Verdict:** Not judged.
- **Limits:** Nine batches, one camera.
- **To tune for your setup:** If a real cloudy night is refused as a whole, raise the cap; if a mismatched limit moves a third of a good session, keep or lower it and fix the limit (STK-01) instead. The `frame_quarantine` gate (`QualityRecords.measure_stack`, or the stack's gate list) reports the measured share and the limit.
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
  1. Read each frame's sky level with `QualityRecords.measure_frames(target, kind="raw_check")`.
  2. Pass the levels to the detector and read the gap ratio it reports.

  ```python
  from astrometricslib import Astrometrics
  from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import detect_background_split

  astro = Astrometrics()
  # One night, one exposure length: narrow the selection with filter_name, since and until.
  check = astro.quality.measure_frames(
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
  report = astro.quality.measure_stack("M 101", compare_to="previous")
  print(report.comparison.before.noise_fraction, report.comparison.after.noise_fraction)
  ```

  `assess_flats` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.assess_flats --> `CalibrationCatalog.assess_flats` is the public form for flats already in the calibration library. <!-- impl: astrometricslib.api.processing.CalibrationCatalog.assess_flats --> The comparison uses `QualityRecords.measure_stack`. <!-- impl: astrometricslib.api.quality.QualityRecords.measure_stack -->
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
  report = astro.quality.measure_stack(weighted.stacked_path, compare_to=equal.stacked_path)
  print(report.comparison.changes["noise_fraction"])
  ```

  `equal_weight_noise_penalty` is an internal function, not part of the public API; may change. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.equal_weight_noise_penalty --> `frame_exposure_seconds` (internal function, not part of the public API; may change) reads each frame's exposure the way the stacker does. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.frame_exposure_seconds -->
- **Acceptance criterion:** The measured gain approaches 1 − 1/P.
- **What to look at:** P (no units; 1.17 means 17% more noise with equal weights) and the relative change in `noise_fraction` between the two stacks (a negative change means the weighted stack is quieter). The measured gain should approach 1 − 1/P.
- **Our result:** M 27, 26 frames of 30 s, 33 of 60 s, 22 of 120 s and 1 of 300 s: P = 1.17. Noise weighting (`-weight=noise`) lowered the measured pixel noise by 13% (date not recorded).

  Frame weights on real data: with `-weight=wfwhm`, the weights rebuilt from Siril's formula on the M 57 stack of 61 frames (night of 2026-10-03, 2026-10-23) have mean 1. The blurriest frame (6.93 px) gets weight 0 and the sharpest (2.09 px, the registration reference) gets 3.18. The effective frame count is 40.8 of 61. The formula is Siril's `compute_wfwhm_weights`, read from its source; it was not compared with weights printed by Siril, which prints none. This part is a design estimate until it is compared with a Siril build that prints them. Weights for `-weight=noise` and `-weight=nbstars` are not rebuilt and are stored as not measured (Siril's noise weight needs a scale factor it does not save in the sequence file).
- **Verdict:** Not judged.
- **Limits:** One stack. Frames limited by read noise rather than sky noise follow a different rule: read noise weights a frame by 1/t² where sky noise weights it by 1/t. The stack therefore stores both effective times, `stacking.effective_time_s` (sky noise) and `stacking.effective_time_read_noise_s` (read noise), so a forecast over frames of unlike lengths can use the read noise of [PHO-03](photometry.md#pho-03). The read-noise time has not been compared with a measurement; it is a design estimate.
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
         quality = astro.quality.measure_stack(result.stacked_path, include=["fwhm", "rejected_fraction"])
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
- **Procedure:** Run the sweep. It stacks the target's luminance frames once for each pair of limits (2.0 to 4.0) and each Siril sharpness filter (`-filter-wfwhm` off, 90%, 80%), then measures the stacked star width and the rejected share with `QualityRecords.measure_stack(path, include=["fwhm", "rejected_fraction"])`.

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
      quality = astro.quality.measure_stack(result.stacked_path, include=["fwhm", "rejected_fraction"])
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
  report = astro.quality.measure_stack("Alcor", kind="spectral", include=["registration"])
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
- **Our result:** Vega session (ZWO ASI533MM Pro, gain 0), measured with the earlier estimator (1.4826 times the median absolute deviation of neighbor differences, before 2026-10-26; the zero shares are unaffected): zero share 71% at 0.5 s, 31% at 1 s, 1% at 2 s, 0.2% at 3 s, 0.02% at 5 s; measured noise 0, 4.2 and 8.4 counts (2, 3 and 5 s all gave 8.4). 0.2 sits between the 1 s group (clipped) and the 2 s group (not).
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
  quality = astro.quality.measure_stack("NGC 2403", include=["rejected_fraction"])
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
  check = diagnostics.measure_frames("M 13", kind="raw_check", limit=300)
  for row in check.frames:
      print(row.get("path"), row.get("star_count"), row.get("line_trail_px"), row.get("line_trail_angle_deg"))
  ```

  `QualityRecords.measure_frames` <!-- impl: astrometricslib.api.quality.QualityRecords.measure_frames --> runs `check_raw_frames` (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.quality.raw_frame_check.check_raw_frames -->
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

### STK-35

**Gain and read noise measured from flat and bias pairs in the library**

- **Claim:** `measure_sensor_noise` reproduces the hand-measured gain and read noise of the ASI 533MM Pro at gain setting 0, offset 10 and 1x1 from the stored calibration frames: the gain to within 0.1% and the read noise to within 1.3%.
- **Settings it supports:**
  - code constants `MINIMUM_FLAT_PAIRS` = 5 and `MINIMUM_BIAS_PAIRS` = 3 in `pipelines/stacking/pre_processing/sensor_noise.py` (changing them means editing the code). Relation: sanity check only. The run used 15 and 5 pairs, so it does not show that fewer pairs suffice. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MINIMUM_FLAT_PAIRS --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MINIMUM_BIAS_PAIRS -->
  - code constant `CENTRAL_REGION_SHAPE_PIXELS` = (1000, 1000) in the same file. Relation: confirmed by this entry. It is the region the hand measurement used. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.CENTRAL_REGION_SHAPE_PIXELS -->
  - code constants `MAXIMUM_TEMPERATURE_SPREAD_C` = 1.0 and `EXPOSURE_RELATIVE_TOLERANCE` = 0.001 in the same file. Relation: sanity check only. The frames passed both. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MAXIMUM_TEMPERATURE_SPREAD_C --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.EXPOSURE_RELATIVE_TOLERANCE -->
- **Status:** Partly measured. One camera, one setting and one temperature, checked against the hand measurement of [PHO-03](photometry.md#pho-03) on the same frames; the pair minimums, the 300 s pairing gap and the linearity refusal are not tested on real data. Synthetic frames of known gain also pass. The pair minimums are not tested below the data used.
- **Data you need:** For one camera setting, at one sensor temperature: 5 or more pairs of flat frames of one exposure and 3 or more pairs of bias frames, all indexed in the calibration library.
- **Procedure:**
  1. Call `measure_sensor_noise` on the calibration catalog for the camera and setting. Leave `save` off to read without writing.
  2. Compare the gain and read noise with a hand measurement ([PHO-03](photometry.md#pho-03)) and check that the gain spread across pairs is small.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.calibration.measure_sensor_noise(
      "ZWO ASI 533MM Pro", 0, 10, "1x1", temperature=-10, telescope="Apertura 75Q", filter_type="L"
  )
  print(result.gain_e_per_adu, result.gain_spread_e_per_adu, result.read_noise_adu, result.read_noise_e)
  ```

  `CalibrationCatalog.measure_sensor_noise` <!-- impl: astrometricslib.api.processing.CalibrationCatalog.measure_sensor_noise --> pairs the flats by their `DATE-OBS` time (at most 300 s apart, so a pair never spans two sessions; `MAXIMUM_PAIR_GAP_SECONDS`) <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MAXIMUM_PAIR_GAP_SECONDS --> and the biases in file-name order, refuses flats above the camera profile's photometric linearity limit, and calls `measure_sensor_noise`. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.measure_sensor_noise -->
- **Acceptance criterion:** The gain agrees with the hand value within 0.5%. The read noise is not given a criterion.
- **What to look at:** The gain in electrons per ADU and its spread across the flat pairs, the read noise in ADU (RMS and from the median absolute deviation) and in electrons, and the pair counts.
- **Our result:** Stored calibration library frames of the ASI 533MM Pro at gain setting 0, offset 10, 1x1, -10 C (mean -9.88 C), 0.55 s flats through the Apertura 75Q; run on 2026-10-22 with the code of branch `94-phase2a-sensor-noise`, on a copy of the configuration, without saving. The hand values are from 2026-10-11 ([PHO-03](photometry.md#pho-03)).

  | Quantity | L flats | SPEC flats | Hand measurement |
  | --- | --- | --- | --- |
  | Gain (e-/ADU) | 0.7823 | 0.7825 | 0.783 (L), 0.782 (SPEC) |
  | Spread across pairs | 0.0024 | 0.0014 | 0.0004 |
  | Flat pairs | 15 | 15 | 15 |
  | Signal (ADU) | 28871 | 25629 | about 28,000 |
  | Read noise, RMS (ADU) | 4.821 | 4.821 | 4.88 |
  | Read noise, MAD (ADU) | 4.193 | 4.193 | 4.19 |
  | Read noise (e-) | 3.772 | 3.772 | 3.8 |
  | Bias pairs | 5 | 5 | 5 |

  The criterion is met by the data: the gain is within 0.1% (0.7823 against 0.783, and 0.7825 against 0.782). The result above was made with pairing by file name; the pairing by time has not been re-run on the real flats. On synthetic flats from two sessions of different sensitivity pattern (11 frames, the first session with an odd count, true gain 0.8 e-/ADU) pairing by file name put one pair across the two sessions and gave a spread across pairs of 0.35 e-/ADU against 0.015 for pairing by time; the medians were within 0.002 of each other.

  The RMS read noise is 1.2% below the hand value. Taking the difference of two frames cancels the bias level, so the mean-bias subtraction is not the cause. The likely causes are that the new code pairs the bias frames in file-name order and takes the median of the five pair values, where the hand figure pooled hand-chosen pairs and the RMS is sensitive to a few hot pixels or cosmic-ray hits in a pair. The MAD figure agrees to 0.1%, which fits that explanation, but the individual pair values of the hand run were not kept, so it is not proven. The gain differs by under 0.1%, inside the pair spread.
- **Verdict:** Not judged.
- **Limits:** One camera, one gain setting and one temperature, and the same frames as the hand measurement, so the two are not independent. Both use one signal level, so neither shows whether the sensor is linear. The spread across pairs (0.0024) is larger than the hand run's 0.0004; the cause is not known, and a pair across two sessions (possible with pairing by file name) is one candidate that the pairing by time removes. The pair minimums were not tested with fewer pairs.
- **To tune for your setup:** If the spread across pairs is large, raise `MINIMUM_FLAT_PAIRS`. Measure each gain setting you observe with, and check the result against a hand measurement the first time.
- **Regenerate with:** The call in the procedure; add `save=True` to write the camera profile.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/pre_processing/test_sensor_noise.py` and `astrometricslib/pipelines/stacking/test/test_sensor_noise_run.py` make flats and biases of known gain (0.8 e-/ADU) and read noise (4 ADU) with Poisson and Gaussian noise and require both back within 2% (3% in the library test).
- **Raw record:** None kept.

### STK-36

**Stack noise in fixed empty apertures, and how it falls with frame count**

- **Claim:** The noise of a stack, measured as the scatter of the summed sky in up to 100 empty patches of 4 pixels radius (chosen by a pixel mask made before placement, away from bright stars and from faint sources found in the stack's own pixels), divided by the flux of 10 reference stars (each measured in an aperture 3 times the star width, as a ratio to its value on the first stack), falls as one over the square root of the effective number of frames. The requirement is agreement within 5% for stacks of 15 to 61 frames. The data in hand cannot test that precision: each spread of 100 patches has an error of 11.7%, and a 5% error needs about 540 independent patches.
- **Settings it supports:**
  - code constants `NOISE_APERTURE_COUNT` = 100 in `pipelines/stacking/post_processing/empty_apertures.py` and `APERTURE_RADIUS_PX` = 4.0 in `pipelines/stacking/post_processing/stack_image.py`. Relation: sanity check only. One target and one night were measured, with these values only. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.NOISE_APERTURE_COUNT --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_image.APERTURE_RADIUS_PX -->
  - code constant `REFERENCE_STAR_COUNT` = 10 (with `MINIMUM_USABLE_REFERENCE_STARS` = 5) in `pipelines/stacking/post_processing/empty_apertures.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.REFERENCE_STAR_COUNT -->
  - code constants `REFERENCE_APERTURE_FWHM_MULTIPLE` = 3.0 in `pipelines/stacking/post_processing/empty_apertures.py` and `MAXIMUM_ROTATION_DEG` = 0.1 in `pipelines/stacking/post_processing/stack_noise.py`. Relation: sanity check only. They are design estimates. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.REFERENCE_APERTURE_FWHM_MULTIPLE --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.MAXIMUM_ROTATION_DEG -->
  - code constants `FAINT_SOURCE_THRESHOLD_SIGMAS` = 3.5, `FAINT_SOURCE_SMOOTHING_SIGMAS_PX` = (1.5, 3.0, 6.0), `FAINT_SOURCE_HIGH_PASS_SIGMA_PX` = 12 and `FAINT_SOURCE_CLEARANCE_PX` = 13 (the sky ring's outer radius plus 1 px, for the rounding of the aperture's center to a pixel) in `pipelines/stacking/post_processing/empty_apertures.py`. Relation: sanity check only. The injection and bias tests below used synthetic fields and one real stack, so the values stay design estimates. At 3.5 the rule changed the noise of pure noise by under 1% (paired against the old placement, measured again with the current code); 3.0 pulled it down by 1 to 2% and 2.5 by 8% (an earlier run), so 3.5 was kept. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_HIGH_PASS_SIGMA_PX --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_THRESHOLD_SIGMAS --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_SMOOTHING_SIGMAS_PX --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_HIGH_PASS_SIGMA_PX --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_CLEARANCE_PX -->
  - code constant `REPLACEMENT_DEPTH_FACTOR` = 2 in `pipelines/stacking/post_processing/empty_apertures.py`. Relation: sanity check only. It is a design estimate. A stored set is replaced when a library stack has at least twice the effective frame count of the stack that chose it; no data tested the factor. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.REPLACEMENT_DEPTH_FACTOR --> Since 2026-10-26 a set is kept per camera, optic and exposure length of the frame the stack is lined up on, and the factor applies within one such set. The reason, on a copy of M 27: a three-group stack lined up on a 120 s frame kept the set chosen on the 60 s stack, its 10 reference stars all saturated in the 120 s frame, and no noise was stored. The fix is checked on synthetic stacks only (`test_stack_noise.py`); the real-data check waits for the forced restack. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.find_library_set_key -->
  - code properties `CameraProfile.usable_peak_level_adu` and `CameraProfile.pixel_saturation_level_adu` in `models/camera_profile.py`, which set the registration reference frame's saturation check (`_saturation_check` in `pipelines/stacking/post_processing/stack_noise.py`) and the saturated pixel count. Relation: sanity check only. The pixel-saturation level is the profile's saturation threshold capped at its clip ceiling, and an assumed clip ceiling still caps. The usable peak is that level capped again at the photometric linearity limit when the profile has one. For the ASI533MM Pro (threshold 65,000 ADU, ceiling 65,532 ADU, linearity limit 60,000 ADU) the pixel-saturation level is 65,000 ADU and the usable peak 60,000 ADU. For the D5300 the profile's threshold is an assumed 65,000 ADU above the measured clip of 16,383 ADU, so both levels are 16,383 ADU, and the check fires only at the clip itself. A saturated pixel fraction is stored with the level it was counted at, and the stage measures it again when that level differs. These are design rules, not measured values. No data set tests them. <!-- impl: astrometricslib.models.camera_profile.CameraProfile.usable_peak_level_adu --> <!-- impl: astrometricslib.models.camera_profile.CameraProfile.pixel_saturation_level_adu -->
  - code constant `SEED_SEARCH_RADIUS_PX` = 500 in `pipelines/stacking/post_processing/stack_noise.py`. Relation: sanity check only. The value was chosen from the one night below, where frame headers disagreed with the true star positions by up to 82 px. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.SEED_SEARCH_RADIUS_PX -->
- **Status:** Partly measured. One target, one night and one camera. The aperture count, aperture radius, reference star count and the 500 px search radius are design estimates (the stacking table). The synthetic test recovers a known noise, with and without faint stars and a nebula, within 10% where the mask's scale is raised by at most about 2 times, and fails where sources dominate that scale (see the masked-regime table). The 5% criterion is not tested at the required precision: on M 57 the three subsets lie 9 to 31% below the prediction, with errors of 14 to 20% on each ratio, and the subsets are nested and share frames with the library stack. The numbers on M 57 used a set defined by the 61-frame library stack (effective count 40.8).
- **Data you need:** A target with at least 40 light frames of one setting from one night, stacked with the stacking pipeline, and the same frames available to stack again as subsets.
- **Procedure:**
  1. Stack the target. The stacking stage measures the noise and stores it as `stacking.empty_aperture_noise_normalized`.
  2. Run the growth job, which stacks subsets of the same frames and measures each one in the same apertures.
  3. Compare each subset's noise with the stack's noise scaled by sqrt(61 / n).

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  astro.processing.measure_noise_growth("M 57")
  for row in astro.quality.query(metric="stacking.empty_aperture_noise_normalized", target_id="M 57"):
      print(row.subject.subject_id, row.value, row.uncertainty)
  ```

  To see the placement, call `analyze_faint_sources` <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.analyze_faint_sources --> on the stack's pixels (the distance of every pixel to the nearest flagged pixel, the mask's noise scale at each smoothing, and the flagged and excluded shares) and `place_empty_apertures` <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.place_empty_apertures --> for the positions; to repeat the bias test, call `place_empty_apertures` on pure noise with and without the mask. To repeat the paired test, call `measure_stack_noise` on the library stack (with `depth=StackDepth(frames, effective_frames)`) and on each subset (with `may_choose_apertures=False`), then `paired_noise_ratio` <!-- impl: astrometricslib.pipelines.stacking.post_processing.difference_noise.paired_noise_ratio --> on their `aperture_sums`, reference fluxes and `aperture_identity`. Delete the target's `quality_apertures.json` (or call `reset_apertures`) so the next stack chooses new apertures with the current rule. A set is also replaced by itself when a library stack has twice the effective frame count of the stack that chose it; the new set has a new identity, and the stored rows of the old and new sets are never compared.

  `ProcessingPipelines.measure_noise_growth` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.measure_noise_growth --> splits the library stack's frames by night and stacks subsets with `stack_frame_subset` <!-- impl: astrometricslib.pipelines.stacking.subset_stack.stack_frame_subset -->. The noise comes from `measure_stack_noise`. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.measure_stack_noise -->
  The aperture set a stack used is stored as the metric `stacking.aperture_set_number` (definition version 1) on every noise stack subject, so that a later comparison of two stacks can tell whether they share one set. The set's identity is the text `v<version>-<12 hex digits>`. It is stored as the whole number `file_version * 2**48 + int(hex, 16)`, which is exact below `2**53` (the version is capped at 31). `aperture_identity_to_number` <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.aperture_identity_to_number --> and `aperture_identity_from_number` <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.aperture_identity_from_number --> convert both ways, and `aperture_set_measurement` <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_quality.aperture_set_measurement --> builds the row. The number has no size or direction and is only compared for equality. Stacks made before this row existed have none, and it cannot be added later: the identity was kept only in the text of a row's reason. A pair of stacks with one of them is therefore not checkable by the forecast check ([WAY-70](wayfinding.md#way-70)). Status: design estimate. Only unit tests have run it (round trip, the 2**53 limit, one row per noise stack); no real stack has been re-measured with it.

- **Acceptance criterion:** Each subset's noise is within 5% of the 1/sqrt(n) prediction made from the full stack.
- **What to look at:** The normalized noise (noise in the empty patches divided by the flux of the reference stars; no units) of the stack and of each subset, with the stated uncertainty. The error of the noise is 1.1664 times the noise divided by sqrt(n) for n patches (the error of a spread taken as 1.4826 times the median absolute deviation); the earlier formula, 1 / sqrt(2 (n - 1)), is that of a standard deviation and was a third too small, 7% instead of 12% for 100 patches.
- **Our result:** Two placements are reported. The aperture placement paragraph at the end of this result is the current one; the table before it was measured with the first placement (apertures away from the 400 brightest stars only) and no longer describes the code. M 57, night of 2026-10-03, 61 frames, ZWO ASI 533MM Pro, 3008 by 3008 pixels. Stack run on 2026-10-23 on scratch copies of the frames with the frame quarantine off; the light frames were not changed. The numbers below were measured again on 2026-10-23 with the corrected error and the new flux scale (reference stars in an aperture of 3 times the star width, as a ratio to the first stack). The stack's normalized noise was 2.65e-4 with an uncertainty of 3.1e-5 (100 patches of 4 px; 11.7% from the noise, the flux scale adds nothing on the stack that defines it). The earlier table used the old scale (4.245e-4), which is not comparable.

  | Frames | Normalized noise | Zero point | 1/sqrt(n) prediction | Difference | In combined sigma |
  | --- | --- | --- | --- | --- | --- |
  | 15 | 5.13e-4 (6.0e-5) | 0.813 | 5.35e-4 (6.2e-5) | -4% | 0.26 |
  | 30 | 4.36e-4 (5.1e-5) | 0.895 | 3.78e-4 (4.4e-5) | +15% | 0.87 |
  | 45 | 2.98e-4 (3.5e-5) | 1.020 | 3.09e-4 (3.6e-5) | -3% | 0.22 |

  The prediction is the stack's value times sqrt(61 / n), with its error scaled the same way. The last column divides the difference by the two errors added in quadrature. The subsets share frames with the full stack, so the real errors of the differences are smaller and these values are upper limits. The 30-frame point lies 15% above the prediction and is within one combined sigma. The first-guess headers of the subsets were moved onto the stored stars by shifts of 4 to 25 pixels, and the stars agreed to 0.2 px rms or better. The subset stacks of this run had no registration reference frame to check saturation against, so only the stack that chose the stars used the camera-profile saturation check. The earlier statement that the differences were "0.4, 0.6 and 3.0 times the errors" used the old error and old flux scale and no longer stands.

  The run also showed that Ekos writes `CRVAL1` and `CRVAL2` equal to the commanded pointing and no plate-solve card, so the frame headers do not say where the stars are. The headers of that night disagree with each other by up to 82 px (frame header predictions differ by 27 px and 82 px; the stars moved by -7 to +6 px in Siril's output). Because of this every stack, with a header solution or without, is moved onto the stored reference stars by a shift before it is measured (`_move_guess_onto_reference_stars`), and the shift, the number of stars that agreed and their leftover position error are written in the reason of the noise rows. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise._move_guess_onto_reference_stars --> The frame's star list for the coordinate check must also keep saturated stars, since the averaged stack keeps them (on this stack 0 of 10 stars matched before that was fixed).
  Aperture placement (2026-10-23, branch `94-noise-aperture-fix`; numbers re-run after the board's fixes). The half-stack work of [STK-38](#stk-38) showed that the first placement left faint stars in the "empty" apertures: 18 of 100 sums lay more than 5 robust sigma from the median, the sums of two disjoint halves correlated at 0.9996, and the largest sum was 26,000 ADU. The placement now also keeps every aperture 13 px (the sky ring's outer radius plus 1 px) from any flagged pixel of a mask made from the stack itself, before any aperture is drawn: the stack minus its 12 px Gaussian-smoothed copy (the large-scale background), smoothed at 1.5, 3 and 6 px, with a pixel flagged when any of the three lies more than 3.5 robust sigma (1.4826 times the median absolute deviation of that smoothed image, taken over the pixels that hold data) above zero. The apertures are chosen by a pixel mask made before placement, not by their own sums. The mask is not unbiased by construction: a 3.5σ noise peak within 1.5 px of an aperture's center can put it inside the excluded zone. The measured bias on pure noise is under 1% (below). On the M 57 library stack the mask flagged 9.1% of the pixels, 79% of the pixels lie within 13 px of a flagged pixel, and 100 apertures were still placed. The set was chosen on the library stack (61 frames, effective count 40.8; a private aperture file, the copy's own file was not changed) and applied to the same stacks:

  | Stack | Frames | Noise, first placement | Noise, current | Expected (STK-38) | Current / expected |
  | --- | --- | --- | --- | --- | --- |
  | Library | 61 | 1.65e-3 | 1.07e-3 (1.2e-4) | 4.30e-4 | 2.5 |
  | Subset | 45 | 1.90e-3 | 1.13e-3 (1.3e-4) | 5.07e-4 | 2.2 |
  | Subset | 30 | 1.86e-3 | 1.12e-3 (1.3e-4) | 6.36e-4 | 1.8 |
  | Subset | 15 | 2.55e-3 | 1.45e-3 (1.7e-4) | 9.47e-4 | 1.5 |

  The noise on the library stack fell by 35%. Of the two halves (odd and even frames), the noise of half A fell from 2.27e-3 to 1.34e-3 (2.1 times the model) and that of half B from 1.86e-3 to 1.12e-3 (1.8 times). The difference of the halves, which cancels what they share, was 1.03e-3 with the first placement and 1.05e-3 with the current one, 1.32 times the model, with an error of 12%. The halves' sums correlate at 0.76 (0.9996 before). Some shared structure is left, so the current noise is still about twice the model's. This run's set differs from the one of the first pass of this work (a 12 px clearance, a scale taken over every pixel), whose library noise was 9.3e-4; the two differ by 1.2 times the error of one measurement, which shows how far a different set of 100 apertures can move one value.

  Paired test of the 1/sqrt(n) rule (`paired_noise_ratio`: the subset's normalized spread over the library stack's, in the same 100 apertures; the error is a bootstrap over the apertures and does not include which frames the subset holds). The prediction is the square root of the ratio of the effective frame counts (40.8 for the library; 8.4, 18.6 and 29.3 for the subsets):

  | Frames | Ratio, subset / library | Prediction | Difference | In sigma of the ratio |
  | --- | --- | --- | --- | --- |
  | 15 | 1.52 (0.31) | 2.21 | -31% | 2.2 |
  | 30 | 1.07 (0.20) | 1.48 | -28% | 2.1 |
  | 45 | 1.07 (0.15) | 1.18 | -9% | 0.7 |

  The ratio is the same whether it is paired or not; pairing changes only the error. All three subsets fall below the prediction. The acceptance criterion (within 5%) is not tested at the required precision: the error of each ratio is 14 to 20%, the subsets are nested (the 15 frames are in every later subset) and share frames with the library stack, so the three points are not independent and their errors correlate. They do not show the 1/sqrt(n) rule holds, and they do not show it fails by a given amount. The point estimates agree in direction with a part of the library stack's noise that does not average away. A resampling of the frames (a restack for each draw) was not done. A single spread of 100 apertures has an error of 11.7%, so a 5% error on one spread needs about 540 independent apertures.

  Injection and bias, on synthetic stacks of 1500 by 1500 pixels with known noise (unit sigma per pixel) plus 9,000 Gaussian stars (FWHM 2.2 px, peaks 0.15 to 30 times the noise of one pixel), a smooth halo with knots and, in the confusion case, 60,000 stars with peaks of 0.03 to 0.3. The truth is the spread of the noise alone at 600 unselected positions; each row is the mean of 6 fields, with 100 apertures (each with an error of 11.7% of its own). The old placement is the first one (no mask). "Flagged" is the share of pixels the mask flagged and "excluded" the share within 13 px of a flagged pixel, where no aperture can go. The last column is the mask's noise scale at 1.5, 3 and 6 px divided by that of the noise alone, which is how far sources raise the effective threshold (the threshold is 3.5 times that scale).

  | Case (white noise) | Flagged | Excluded | Old / truth | New / truth | Mask scale / noise-alone scale |
  | --- | --- | --- | --- | --- | --- |
  | Faint stars | 6.9% | 75% | 1.33 | 1.05 (0.05) | 1.2, 1.5, 2.1 |
  | Faint stars and nebula | 6.9% | 75% | 1.33 | 1.04 (0.05) | 1.2, 1.5, 2.1 |
  | Faint stars, nebula and confusion | 6.9% | 74% | 1.28 | 1.02 (0.05) | 1.2, 1.5, 2.1 |
  | 20,000 stars | 8.4% | 90% | 1.71 | 1.15 (0.07) | 1.5, 2.3, 3.7 |
  | 40,000 stars | 8.8% | 96% | 2.94 | 1.35 (0.05); 51 to 77 apertures kept | 2.1, 4.0, 6.0 |
  | Faint stars and nebula, noise correlated over 0.8 px | 2.8% | 58% | 1.12 | 1.06 (0.06) | 1.1, 1.2, 1.3 |
  | 20,000 stars, noise correlated over 0.8 px | 4.3% | 80% | 1.29 | 1.04 (0.04) | 1.2, 1.4, 1.7 |
  | 40,000 stars, noise correlated over 0.8 px | 5.4% | 92% | 1.73 | 1.13 (0.06) | 1.4, 1.9, 2.4 |

  The masked regime (70 to 80% of the pixels excluded) is rows 1 to 3 and 7 (74 to 80% excluded): the new estimate stays within 5% of the truth there and the old one is 28 to 33% high. When the excluded share reaches 90% or more the estimate rises (1.15, then 1.35 with fewer than 100 apertures left). Where sources raise the mask's scale more, the mask misses them. A smooth texture (a correlated random field of 4 px scale over 80% of the frame, with 1% faint stars) with an amplitude of 0.1, 0.2, 0.4 and 0.8 times the pixel noise gives, for white noise, new / truth of 1.07, 1.27, 1.74 and 2.89 (old: 1.45, 1.65, 2.11, 3.44) with the mask's scale at 6 px raised 1.8, 2.3, 3.6 and 6.4 times and only 1 to 3% of the pixels flagged. So once sources raise the 6 px scale by more than about 2 times, the placement leaves most of the structure in the apertures. In a crowded field the effective threshold is 3.5 times the raised scale, for example 4 to 7 times the noise of the smoothed image for the faint-star rows. On M 57 the scale is 10 to 30 times that of the white-noise model (STK-38), far outside every injected case here, so these injection results do not show the estimate is unbiased on M 57. Bias on pure noise: with 600 apertures per field (error 1.5% over 10 fields), the new placement compared with the old on the same fields (paired ratio) was 0.999 (+-0.004) for white noise and 0.996 (+-0.006) for noise correlated over 0.8 px at a threshold of 3.5, so the rule pulls pure noise down by less than 1% (consistent with 0); at 3.0 it was 0.991 (+-0.007) and 0.979 (+-0.009), and at 2.5 it was 0.919 (+-0.013) for correlated noise (an earlier run, clearance 12 px). The rule keeps all 600 apertures on pure noise.

  What the rule does not remove: on the real stack the noise stays at 1.5 to 2.5 times the model with a shared part between the halves. Lowering the threshold to 3.0, 2.5 or 2.0 did not lower the library stack's noise by more than 11% in the first pass (9.3e-4 to 9.3e-4, 8.3e-4 and 8.5e-4) while the pure-noise bias reached -8% at 2.5. The separating tests of [STK-38](#stk-38) (sign of the flagged structure, skewness, growth with aperture radius, the flat) were run on these data and do not point to one cause.

  Memory of the mask (3008 by 3008 and 6000 by 4000 pixel stacks, a float64 plane passed in, each case in a fresh process): the rise of the peak over the process size after the plane was made was 519 MB and 1,374 MB before the change (float64 arrays) and 346 MB and 917 MB after (32-bit working copies, arrays freed as they are used). The Euclidean distance transform is the likely remaining peak; it was not profiled. The test call is in the procedure; the scripts are in the session's scratch folder (`science/aperture/`).
- **Verdict:** Not judged.
- **Limits:** One target, one night, one camera. The faint-source mask removes sources found in the stack itself, so it cannot remove structure below its threshold, and it is tuned (3.5σ) on one stack and on synthetic fields only; the injection test used Gaussian stars and a smooth nebula. A very crowded or very nebulous field may leave fewer than 20 clean apertures, and then the noise is not measured (never the first placement); the reason gives the share of the image the mask flagged and the share its clearance excludes. The 500 px search radius comes from this one night. The subsets share frames with the full stack, so their agreement with 1/sqrt(n) is not an independent test of the noise estimate. A translation is applied; a rotation is only estimated, and a stack rotated by more than 0.1 degree is refused (a design estimate, never met on real data). The flux scale and the large reference aperture have been run on this one target only. Definition versions (2026-10-23): `stacking.empty_aperture_noise_stack_unit`, `stacking.noise_reference_flux_stack_unit`, `stacking.empty_aperture_noise_normalized`, `stacking.noise_without_night` and `stacking.noise_difference_without_night` are at definition version 3: the uncertainty is 1.1664 × spread / √n, apertures are at least 24 px apart, the reference flux is measured in a large aperture and scaled by each star's ratio to the first stack, the apertures are placed away from faint sources, and every set of apertures has an identity that the stored rows name. Rows of versions 1 and 2 (scratch copies only) are not comparable. Rows of different sets are never compared: a set is replaced when a library stack has at least twice the effective frame count of the stack that chose it, and a subset stack never chooses or replaces a set (it reports that the apertures are missing or predate version 3). The sample for the mask's noise scale is the pixels that hold data; on a crowded or nebulous field the scale is raised by the sources themselves and the mask then misses them (see the masked-regime rows). The mask's noise scale at each smoothing and its flagged share are kept with each set. Readers can leave subset stacks out with `quality.query(library_only=True)` or pick one with `variant=`.
- **To tune for your setup:** If the check of the reference stars fails on your stacks, raise `SEED_SEARCH_RADIUS_PX` to the largest disagreement between your frame headers and the stars. More apertures make the noise estimate steadier.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_stack_noise.py` recovers a known white noise within its stated uncertainty on a synthetic stack, keeps apertures away from a source far below the star finder's limit, checks that the mask leaves pure noise unbiased, recovers the noise under faint stars and a nebula within 10% where the old placement is 15% or more high, refuses to fall back to the old placement when no clean apertures remain and stores the masked share in the not-measured row, keeps the mask's noise scale from being pulled down by an empty border, and covers the set identity, the replacement of a set by a stack with twice the depth, the refusal of a subset stack to choose or to read a version-2 file, and the refusal to pair sums of different sets (`test_subset_noise_run.py` and `test_stacking_record_quality.py` cover the subset and the stored rows). The real-data run has no test.
- **Raw record:** None kept; the rows were stored in the quality store of the run.

### STK-37

**Subset stacks: each night's contribution**

- **Claim:** The normalized noise of the library stack without one night, compared with the noise of the full stack, shows whether that night helped, and each night's share of the summed frame weights says how much it should have helped.
- **Settings it supports:**
  - code constant `MINIMUM_SUBSET_FRAMES` = 4 in `pipelines/stacking/subset_stack.py`. Relation: sanity check only. It is the smallest sigma-clipping sample where one frame can be rejected and the rest still give a spread. <!-- impl: astrometricslib.pipelines.stacking.subset_stack.MINIMUM_SUBSET_FRAMES -->
  - code constants `DEFAULT_GROWTH_STEPS` = 4 and `SINGLE_NIGHT_FRACTIONS` = (0.25, 0.5, 0.75) in `pipelines/stacking/subset_noise_run.py`. Relation: sanity check only. Neither value has been tested. <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.DEFAULT_GROWTH_STEPS --> <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.SINGLE_NIGHT_FRACTIONS -->
- **Status:** Design estimate. The growth series of one night is measured in [STK-36](#stk-36); leaving a night out has been run on synthetic frames only, and no real target with several nights has been run. The comparison with the library stack uses the paired error (apertures resampled together), the night's share is stored as assumed when the library stack's member list is, and each leave-out stack stores the ratio its own effective frame count predicts (`stacking.subset_expected_noise_ratio`). None of these has been run on real frames of several nights.
- **Data you need:** A target with frames from at least two observing nights, stacked once, with its aperture file beside the stacks.
- **Procedure:**
  1. Call `measure_night_contributions` for the target.
  2. Read `stacking.noise_without_night` and `stacking.night_weight_fraction` for each night.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  astro.processing.measure_night_contributions("M 13")
  for name in ("stacking.noise_without_night", "stacking.night_weight_fraction"):
      for row in astro.quality.query(metric=name, target_id="M 13"):
          print(name, row.subject.subject_id, row.value)
  ```

  `ProcessingPipelines.measure_night_contributions` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.measure_night_contributions --> runs `run_night_contributions`. <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.run_night_contributions -->
- **Acceptance criterion:** Not recorded (set after the first real result).
- **What to look at:** The noise without the night against the noise of the full stack (a higher noise without the night means the night helped), and the night's weight share.
- **Our result:** Synthetic test only (`test_subset_noise_run.py`: a stacker with noise 6/sqrt(n); the two-night subset has 0.71 times the one-night subset's noise, within 30%). Not yet run on a real target.
- **Verdict:** Not judged.
- **Limits:** Siril's own frame filters can drop frames from a subset without naming them. The effective frame counts of such subsets are then stored as assumed. The cost of one leave-out run (about one to three library stacks) is an estimate.
- **To tune for your setup:** Raise `MINIMUM_SUBSET_FRAMES` if small subsets reject too few frames.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_subset_noise_run.py`.
- **Raw record:** None kept.

### STK-38

**The noise a stack's frames predict in an empty aperture**

- **Claim:** The noise in an empty aperture that the sky's light and the camera's read noise give, worked out from each frame's sky, read noise, gain and weight, is the part of the measured noise that averaging frames removes. A measured noise above it is expected, because some things repeat in every frame and so do not average away: faint stars and nebulosity, fixed patterns, and the noise of the master calibration frames.
- **Settings it supports:**
  - code constant `STACK_UNITS_PER_ADU` = 1/65535 in `pipelines/stacking/post_processing/expected_noise.py`, which is `1 / FULL_SCALE_COUNTS` of the exposure-group combine, so the two cannot drift. Relation: derived from this entry. Siril divides every integer frame by 65535, a 14-bit raw frame included (stored without scaling up), and a frame stored as floating-point numbers would break the scale; nothing checks for one. The frames' own sky and the stack's median agree to 0.1% on the stack below. <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.STACK_UNITS_PER_ADU -->
  - the aperture multiplier `effective_aperture_area_px` (about 59 px: about 46 for the squared overlaps of the circle and about 13 for the median of the sky ring, which assumes Gaussian noise in the ring). Relation: sanity check only. It is arithmetic on the aperture shape, and one simulation recovers a known noise within 2%. <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.effective_aperture_area_px -->
- **Status:** Partly measured. A simulated stack of known gain, read noise, sky and weights is recovered to within 2%. On the real M 57 stack the model reproduces the stack's sky level. The difference of two half stacks, which cancels what the halves share, is 1.2 times the prediction (about 12% error), and the difference of two neighboring single frames is 1.1 times it. With the first aperture placement the measured noise of a stack was 2.6 to 3.9 times the prediction. Placing the apertures away from faint sources ([STK-36](#stk-36)) lowered it to 1.5 to 2.5 times (library stack 3.9 to 2.5), so sources the mask can find were a large part of that structure, but not all of it: the halves' sums still correlate at 0.76, and the difference of the halves is 1.32 times the prediction. Four separating tests (below) do not point to one cause. The difference of the halves is now a stored measurement with its own entry ([STK-42](#stk-42)): the library stack's random noise it implies is 1.32 times the prediction and the empty-aperture noise is 1.88 times that.
- **Data you need:** A target with a stack of at least 20 light frames of one setting, the frame measurement job run on those frames (it stores the sky of each frame), and the camera profile entry for the setting (gain and read noise).
- **Procedure:**
  1. Run the frame measurement job, then stack the target. The stacking stage stores the expected noise next to the measured noise, and the sky level the frames predict next to the stack's own median.
  2. Read both for the stack and for the subsets of the growth job.
  3. Divide the measured noise by the expected noise, and look at how the ratio changes with the frame count.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  astro.processing.record_frame_measurements("M 57")
  astro.processing.stack("M 57", force=True)
  astro.processing.measure_noise_growth("M 57")
  for name in (
      "stacking.empty_aperture_noise_stack_unit",
      "stacking.expected_sky_noise_stack_unit",
      "stacking.expected_read_noise_stack_unit",
      "stacking.expected_empty_aperture_noise_stack_unit",
      "stacking.expected_sky_level_stack_unit",
      "stacking.stack_sky_level_stack_unit",
  ):
      for row in astro.quality.query(metric=name, target_id="M 57"):
          print(name, row.subject.subject_id, row.value, row.status)
  ```

  `expected_stack_noise` <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.expected_stack_noise --> combines the frames; `compute_expected_noise` <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_science.compute_expected_noise --> reads each frame's stored sky and the camera profile.

  To repeat the two tests of the gap, use the scripts of the run (scratch copies only): `make_halves.py` stacks the odd and even frames with the library stack's registration reference frame in both halves, without trimming, using `run_stack` <!-- impl: astrometricslib.pipelines.stacking.stack_runner.run_stack -->, and `analyse_halves.py` puts the target's stored apertures on the halves and on their difference with `stack_noise._sums_at` and compares them with `compute_expected_noise` of each half. `single_frame_noise.py` applies `signed_aperture_sum` <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_image.signed_aperture_sum --> to raw frames at the stored aperture positions, moved by Siril's registration shift of the frame.
- **Acceptance criterion:** Not recorded (set after the causes of the gap are separated).
- **What to look at:** The sky part and the read part of the expected noise (the read part is about a fifth of the sky part here), and the ratio of the measured noise to the expected total. A ratio near 1 means the model explains the stack. A ratio far above 1 that stays when frames are added means structure that repeats from frame to frame. The row for a stack made with a measured gain and sky is a model prediction from measured inputs, not a measurement of the stack.
- **Our result:** M 57, night of 2026-10-03, ZWO ASI 533MM Pro (gain 0.783 e-/ADU, read noise 3.8 e-, both measured), 60 s frames, 61 frames in the library stack. Run on 2026-10-23 on scratch copies; the light frames were not changed. Noise values are in the stack's units (one ADU is 1/65535). The tables below were measured with the first aperture placement (away from bright stars only), which left faint stars in the apertures; the update after them gives the ratios with the current placement.

  | Stack | Frames | Effective frames | Measured noise | Expected sky part | Expected read part | Expected total | Measured / expected |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | Subset | 15 | 8.4 | 2.44e-3 (2.8e-4) | 9.26e-4 | 1.97e-4 | 9.47e-4 | 2.6 |
  | Subset | 30 | 18.6 | 1.78e-3 (2.1e-4) | 6.22e-4 | 1.32e-4 | 6.36e-4 | 2.8 |
  | Subset | 45 | 29.3 | 1.85e-3 (2.2e-4) | 4.96e-4 | 1.05e-4 | 5.07e-4 | 3.6 |
  | Library stack | 61 | 40.8 | 1.66e-3 (1.9e-4) | 4.21e-4 | 8.9e-5 | 4.30e-4 | 3.9 |

  The unit check: the weighted mean of the frames' sky above the bias is 411.8 ADU; the median of the stack is 0.006278, which is 411.4 ADU at 1/65535. Every stack now stores both skies, so the check repeats on the subsets: the sky the frames predict against the stack's median is 0.006239 against 0.006253 (15 frames), 0.006273 against 0.006279 (30) and 0.006289 against 0.006277 (45), within 0.2%, and 0.006284 against 0.006278 for the library stack. The expected noise falls as one over the square root of the effective frame count (a factor 2.2 from 15 to 61 frames).

  The table cannot be read as a growth curve. The raw measured noises are not on one flux scale: the subsets' reference stars have zero points of 0.81, 0.90 and 1.02 against the library stack ([STK-36](#stk-36)). STK-36 divides by the reference flux, and that normalized noise follows one over the square root of the frame count to within 5% (one point 15% high, within one combined sigma). Divided by the zero points, the raw noises are 3.0e-3, 2.0e-3, 1.8e-3 and 1.7e-3, which fall by a factor 1.8 from 15 to 61 frames, against 2.0 for the frame count and 2.2 for the effective frame count. That is no clear floor. The subsets are also nested (the 15 frames are in every later subset), so the four points are not independent, and a fit of a floor and a scale to them (an earlier version found 1.4e-3 and 2.1) is an indication of nothing. The raw subset ratios above carry up to 20% from the flux scale.

  A fixed pattern from the calibration frames cannot make a floor of that size. A floor of 91 ADU in an aperture sum, if it came from independent pixels, needs about 12 ADU per pixel (3% of the 411 ADU sky). The master flat adds 0.5 ADU per pixel (0.119% of the sky, [STK-39](#stk-39)), and the master bias or dark of N frames adds about 4.8 / sqrt(N) ADU per pixel (the read noise of one frame in ADU). Faint stars and nebulosity in the apertures are the likelier cause of any floor. The M 57 night was dithered every 10 exposures by about 1.7 px or more and otherwise drifted about 13 px an hour ([STK-44](#stk-44)); this argument rests on the size of the pattern and not on the pointing being fixed, so it still holds. The dither and drift would, if anything, smear a calibration pattern a little.

  The half-stack difference. The 61 frames, in time order, went alternately to two halves, and the frame the library stack was registered to (`M_57_Light_Luminance_050.fits`) went into both, so both halves were registered to the same frame and their pixels line up without resampling (no trim; a phase correlation of the two stacks finds a shift of 0.00 px in both axes at 1/100 px resolution). The halves share that one frame. It is the sharpest frame, so it has the largest star-width weight in both halves, and its common part takes 22% from the sum of the two expected variances; the model removes it (if it did not, the measured difference would be 1.06 times the prediction). Half A had the 31 frames of even place and the shared frame (32 stacked) and half B the 30 of odd place, which include it (30 stacked); each was weighted by Siril's star-width weights, rebuilt for the model. The target's 100 stored apertures were put on the halves as a normal stack is measured, and on the difference A minus B.

  | | Measured noise (error 12%) | Expected | Measured / expected |
  | --- | --- | --- | --- |
  | Half A | 2.31e-3 | 6.38e-4 | 3.6 |
  | Half B | 1.78e-3 | 6.36e-4 | 2.8 |
  | Difference A - B | 9.53e-4 | 7.98e-4 (root sum of squares, less the shared frame) | 1.19 |

  The sums of the two halves over the same apertures are strongly alike: the rank correlation is 0.88, and over the 82 apertures within 5 robust sigma of the median the correlation is 0.90, with a shared part of 146 ADU against 155 and 151 ADU in the halves. The distribution of the halves' sums has a long upper tail (skewness 8; 18 of the 100 apertures lie more than 5 robust sigma from the median in at least one half; the largest sum is 26,000 ADU, a star too faint to be left out when the apertures were chosen), which a symmetric pattern from a flat or a dark would not give and faint stars or nebulosity would. The difference has a tail too (skewness 3.9), so some of that structure changes between the halves (seeing, the dither). With only 100 apertures the measured ratio is 1.19 plus or minus 0.14.

  The single-frame test. Five raw frames spread through the night (time order 0, 15, 30, 45 and 60) were measured in the same apertures, moved by Siril's registration shift of each frame. Raw, dark-subtracted (the median of the library's 30 darks of 60 s) and dark-subtracted and flat-fielded (the mean of the library's 30 L flats less the median of 20 bias frames): the first leaves in everything (bias level, dark current, hot pixels, the flat pattern), the second leaves in the flat pattern and the third leaves in what dividing by this master flat does not remove. The noise of the pixels themselves, from differences of neighbors, is 1.08 times the model's (25.2 against 23.3 ADU on a typical frame). The empty-aperture noise of a single frame is 1.2 to 1.9 times the model (median 1.7 over all 61 frames dark-subtracted, 16th to 84th percentile 1.4 to 1.9), and the three versions agree within 20%. The same measurement on the difference of two neighboring frames that lie within 0.1 px of each other (four pairs, each root-two larger by the model) gives 1.38, 1.14, 1.07 and 0.79 times the model, median 1.11. A single frame's gap to the model is therefore the same structure that the halves share, not noise of the individual frame. The number of pairs is small and each has an error of 12%.
  Update with the faint-source placement ([STK-36](#stk-36), 2026-10-23, branch `94-noise-aperture-fix`, re-run after the board's fixes). The apertures were placed again on the library stack with the faint-source mask and applied to the same stacks. The measured noise over the expected total is now 1.5 (15 frames), 1.8 (30), 2.2 (45) and 2.5 (61 frames), against 2.7, 2.9, 3.8 and 3.8 with the first placement; the halves are 2.1 (A) and 1.8 (B), against 3.6 and 2.9. The halves' correlation fell from 0.9996 to 0.76. The difference of the halves was 1.03e-3 (first placement) and 1.05e-3 (current), 1.29 and 1.32 times the model with a 12% error, against 1.19 in the run above, which used a slightly different shift for the stored apertures. So the sources the mask can find explained part of the gap in the stack noise, the difference still sits about 1.3 times above the model, and about 2 times of the stack noise is shared structure the mask did not find. The ratio rises with the frame count (1.5 to 2.5), which fits a part of the noise that does not average away, but with nested subsets and a 12% error on each point this is a hint, not a fit.

  Separating tests on the stored data (2026-10-23; the M 57 library stack and the two half stacks, with the 100 apertures chosen in [STK-36](#stk-36); the call is `science/aperture/real_v3.py` in the session's scratch folder, which uses the stacking functions named in STK-36). They ask what the structure left in the apertures is. They were not run on a second target.

  - Sign of the flagged structure. A faint star is one-signed (positive) and random noise is symmetric; a flat-field residual or a sky mottling has both signs. Run on the negated stack, the mask flags 16.2% of the pixels, against 9.1% for the stack itself. The negated side is larger because the 12 px background subtraction leaves negative rings around bright stars and the nebula. Away from the 400 brightest stars (more than 36 px from any of them, 84% of the image) the two signs flag 8.0% and 6.7%, a ratio of 1.2 to 1. White noise of the same size flags 0.06% on each side. The mask's noise scale is 9.8, 18.5 and 30 times the scale a white-noise model gives at 1.5, 3 and 6 px (the model's per-pixel noise from the expected aperture noise), so the effective threshold on this stack is 34, 65 and 105 times the smoothed white-noise sigma. For the difference of the halves, which holds no shared sky structure, the scale is 1.5, 1.5 and 1.3 times the model and the two signs flag 2.2% and 1.8%. So the structure near the mask's scale has both signs in about equal share, which a population of faint stars alone does not give; it fits structure of several pixels with both signs, such as a flat or background residual, extended nebulosity or correlated noise. The test does not separate those. It does show that the mask finds sources far above the noise, not those near it, because sky structure sets its scale.
  - Skewness of the sums. The library stack's 100 sums have a skewness of 1.75, half A's 1.34 and half B's 1.57, against 0.41 for A minus B (the error of a skewness from 100 normal values is 0.24). With the first placement it was 8. A positive tail shared by both halves remains, which is what sources or nebulosity would give and a symmetric pattern would not, but its size is now modest and the sums are only 100.
  - Spread against aperture radius (2 to 6 px, same positions and ring). For white noise the spread grows as the square root of the effective area, a factor 1.00, 1.61, 2.29, 3.06, 3.93 from radius 2 to 6 px (a log-log slope of 1.24). The measured spread of the library stack grows 1.00, 1.80, 2.96, 4.05, 4.85 (slope 1.47), that of half A 1.00, 2.09, 3.22, 4.42, 6.07 (slope 1.62) and that of A minus B 1.00, 2.15, 3.21, 4.47, 5.98 (slope 1.60). The difference, which cancels shared sky, grows as steeply as the stack, and the ratio of the difference's spread to the library stack's is 0.91, 1.08, 0.99, 1.00 and 1.12 across the radii. So the pixel noise is correlated over a few pixels (the aperture sums add up faster than for independent pixels), which raises the sums above the white-noise model without any shared structure. This test does not separate structure from correlated noise. Faint stars (2 px wide) would add a part that stops growing beyond about 3 px; none is seen between 4 and 6 px.
  - The flat. The master flat's random noise adds 5.1e-5 to an aperture sum ([STK-39](#stk-39)): 4.8% of the measured noise (1.07e-3) and 12% of the expected (4.3e-4). The excess variance of the stack over the model is 9.6e-7, and the flat's variance (2.6e-9) is 0.27% of it. The flat's random noise is therefore not the cause. A systematic mismatch of the flat (dust, illumination) is not part of that budget and was not tested.
  - What the numbers support: the master flat's random noise is excluded (0.3% of the excess variance); correlated pixel noise accounts for at most a factor of about 1.3 over the white-noise model (A minus B is 1.32 times it, and it may hold other changes between the halves too) and is not enough for the stack, which is 2.5 times; the structure left is two-signed at the mask's scale and a modest positive tail remains in the sums. A single cause is not identified, and no test here separates a flat or background residual from extended nebulosity.
  - The difference as a stored measurement (2026-10-23, [STK-42](#stk-42)). The half-difference job measures the noise of the difference in the same apertures, with the shared frame's effect on the variance included (variance factor 0.0845 instead of 0.108). The random noise it implies for the library stack is 5.68e-4, 1.32 times the 4.30e-4 expected, and the library stack's empty-aperture noise is 1.88 times that. Letting each frame have its own modeled noise (the variance ratio R of STK-42, 3.442 against 3.445 for equal frames) gives the same 5.68e-4, so the frames' unequal sky does not explain the excess. An injection test with known noise on shared structure recovers the noise within 12% on average where the plain noise reads up to 8.5 times too high. So the part of the stack's noise that more frames remove sits 1.32 times above the white-noise model, and about half of the measured empty-aperture noise is shared structure.

  - Siril's normalization scale (2026-10-26, decision D9; library copy, code of branch `134-group-weights-nikon` after commit cbd4229e, in-sample). Siril's `-norm=addscale` multiplies each registered frame by an unsaved scale that matches its pixel spread to the reference frame's; resampling lowers a frame's spread, so the scale is above 1 for every frame but the reference. The library now measures the stack's scale as the median, over 10 unsaturated reference stars, of their flux in the stack over their flux in the registration reference frame, divided by the ratio with no scaling, and multiplies the expected noise by it (`stacking.normalization_scale`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.normalization_scale.measure_normalization_scale --> On the 60 s stacks the scale was 1.193 ± 0.006 (M 27, 33 frames), 1.385 ± 0.007 (M 57, 61 frames) and 1.339 ± 0.005 (M 81, 30 frames). The half-difference job's random part over the unscaled frame prediction gave 1.14 ± 0.13, 1.37 ± 0.16 and 1.34 ± 0.16 (error from the aperture error 1.17 / sqrt(100) per value): agreement within 0.4, 0.1 and 0.0 combined errors. The scaled prediction over the half-difference random part is 1.01, 1.05 and 1.02. The scale's error is star scatter only; frames dimmed by cloud or haze bias it low. A group stack is corrected with its reference group's scale, checked on synthetic stacks only. Each frame's own scale is not recorded, so `stacking.frame_weight` stays the star-width share; that biases noise ratios by under about 3% (design estimate).
- **Verdict:** Not judged.
- **Limits:** One target, one night, one camera, one Galactic field. The model leaves out Siril's normalization of each frame, debayering of color stacks, the noise of the master dark, bias and flat, fixed patterns, the data that pixel rejection removes and faint stars in the apertures. It treats the pixels of an aperture as independent and assumes the stack is the weighted mean with the reported weights. A stack combined from several exposure groups gets the group formula (2026-10-25, issue #134: each frame counts W_g w_i / (sum W x G_g) in counts per second, the noise is scaled by the mean exposure per frame, the predicted sky is the heaviest group's sky per second times that mean). It is computed, not yet validated: results not recorded, status unchanged. A stack with a group clipped at zero gets none, and a clipped single-group stack too, on purpose. A spectral stack gets none, and a stack whose member list is uncertain gets an assumed one. A stack whose weights Siril does not report (noise or star-count weighting) has none. The expected rows are assumed when the gain in the camera profile or the sky is not measured. No uncertainty is reported, because the errors of the gain and the sky are not propagated. The unit scale is checked by the stored pair of sky levels on every stack, but was compared on this one stack only.
- **To tune for your setup:** Run the frame measurement job first. Add your camera's gain and read noise at your setting with `CalibrationCatalog.measure_sensor_noise`. To see how much of your stack's noise is shared structure, stack two disjoint halves of your frames on one registration reference and compare their difference with the prediction.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_expected_noise.py` recovers a known noise from a simulated stack within 8% (it came out 2% low) and checks every missing-input path; `test_record_science.py` checks the join with the stored sky rows and the camera profile with a fake store, the group, clipped, spectral and uncertain-member cases (the group formula against an independent sum, and its reduction to the single-group result) and the failure paths.
- **Raw record:** None kept; the rows were stored in the quality store of the run. The scripts and their output are in the session's scratch folder (`science/half_stack/` and `science/single_frame/`).

### STK-39

**The noise the master flat adds to a stack**

- **Claim:** The master flat's noise, blurred by the width the stage applies, adds a fixed pattern to an aperture sum that this module can work out from the flat's frame count, its noise before the blur, the blur width and the stack's sky level, in the same units as the stack's measured noise.
- **Settings it supports:** None. The figures are derived and the blur width limits are the design estimates STK-D06.
- **Status:** Partly measured. The blur factor matches the Gaussian formula and a simulated field. The aperture figure matches a simulation to within 11% (the sky ring, which the figure leaves out). On the real stack the figure is small, but the master flat itself was not measured; its noise is derived from the flat assessment.
- **Data you need:** A stack calibrated with flats, with its flat assessment stored (the frame count, the noise and the blur width), and the stack file for its sky level.
- **Procedure:**
  1. Stack the target (the stage stores the flat's noise next to the stack's noise).
  2. Read the flat's noise in the aperture, and the stack's noise.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for name in (
      "stacking.flat_frame_count",
      "stacking.flat_noise_fraction",
      "stacking.flat_smoothing_sigma_px",
      "stacking.flat_noise_after_smoothing_fraction",
      "stacking.stack_sky_level_stack_unit",
      "stacking.flat_noise_in_aperture_stack_unit",
      "stacking.empty_aperture_noise_stack_unit",
  ):
      for row in astro.quality.query(metric=name, target_id="M 57", library_only=True):
          print(name, row.value)
  ```

  `flat_noise_contribution` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution.flat_noise_contribution --> turns the stored facts into the two noise values. `assess_flats` <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.assess_flats --> gives the facts.
- **Acceptance criterion:** Not recorded.
- **What to look at:** The flat's noise in the aperture next to the stack's measured noise and next to the expected noise ([STK-38](#stk-38)). A flat whose figure is a large share of the stack's noise is limiting the stack, and more flat frames or a wider blur would help.
- **Our result:** M 57 library stack, 2026-10-23: 30 flat frames, noise of the average 0.119% of the flat's level before any blur (below the 0.5% limit, so no blur: the figure after the blur is the same, 0.119%), stack sky level 0.006278 (411 ADU). The flat adds 5.1e-5 to an aperture sum. The stack's measured noise is 1.66e-3 and the expected noise 4.3e-4. So the flat is 3.1% of the measured noise and 12% of the expected noise, and adds about 0.1% to the measured variance. In simulation, the blur factor for a blur width of 2 px is 0.141, which equals 1 / (2 × width × sqrt(pi)) to 0.003%; the aperture figure for a flat noise of 2% on a sky of 0.006 is 8.1e-4 against 9.1e-4 simulated without a blur and 6.5e-4 against 6.9e-4 simulated with a 1.5 px blur.
- **Verdict:** Not judged.
- **Limits:** The master flat is not kept by the stacking step, so its noise after the blur is derived (the assessment's noise times the blur factor), not measured on the master. The figure takes the whole pattern to stay (no dithering) and leaves out the sky ring, which in simulation put the sum 7% to 11% above it. The flat assessment's noise rests on the first two flat frames and on a cap of 32 frames averaged. One stack and one flat set were run.
- **To tune for your setup:** Take more flat frames if the figure is more than a few percent of your stack's noise.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_flat_noise_contribution.py`.
- **Raw record:** None kept.

### STK-40

**The brightest star's peak against the ceiling, for each exposure group**

- **Claim:** At the exposure that was used, the brightest star's peak above the sky, divided by the room between the sky and the ceiling, says how close the exposure came to clipping. A value below 1 is a measured peak. A clipped star's peak is estimated from the pixels around its core: it says that the star clipped, and its size is stated only inside the range that [STK-41](#stk-41) checked.
- **Settings it supports:**
  - code constants `CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA`, `CHECKED_ESTIMATE_ERROR_FRACTION`, `CHECKED_ESTIMATE_SHARE_WITHIN_ERROR` and `CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS` in `pipelines/stacking/post_processing/exposure_saturation.py`. Relation: derived from [STK-41](#stk-41). <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_ERROR_FRACTION --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_SHARE_WITHIN_ERROR --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS -->
  - code constant `MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO` = 100 in the same file, which stops a frame's estimate from being used at all beyond 100 times the room. Relation: sanity check only. [STK-41](#stk-41) finds the proportionality of estimates holding to 80 times the room on the ASI533MM Pro, so 100 is a little lenient, and it holds nowhere on the D5300. It is not changed here, because the old recommended exposure uses it. <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO -->
- **Status:** Partly measured. An unclipped frame gives a measured peak. A clipped frame gives an estimate. Inside the range checked for the camera it is stored as assumed, with the error of the check in the reason. Beyond that range (and for any camera with no range) the fraction and the peak are stored as not measured, with the size of the estimate, and only the count of clipped frames is stored.
- **Data you need:** A stack with the raw frames still on disk, and a camera profile that gives the clip ceiling.
- **Procedure:**
  1. Stack the target. The stage reads up to three raw frames of each exposure group and stores the peak values on the group's entry of the summary, and the rows from it on the group's stack.
  2. Read the fraction and its parts.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for name in (
      "stacking.group_brightest_star_peak_fraction",
      "stacking.group_brightest_star_peak_above_sky_adu",
      "stacking.group_raw_sky_level_adu",
      "stacking.group_clip_ceiling_adu",
      "stacking.group_linearity_limit_adu",
      "stacking.group_peak_estimated_frame_count",
      "stacking.group_clipped_frame_count",
  ):
      for row in astro.quality.query(metric=name, target_id="M 57", library_only=True):
          print(name, row.value, row.status, row.sample_count, row.reason)
  ```

  `summarize_group_peak` <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak --> takes the median over the sampled frames of what `measure_frame_saturation` <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.measure_frame_saturation --> finds.
- **Acceptance criterion:** Not recorded.
- **What to look at:** The count of clipped frames first (it is stored whatever the estimate), then the fraction, whether it is assumed or not measured, the number of frames behind it, and the ceiling. The photometric linearity limit (60,000 ADU for the ASI533MM Pro) is stored beside the clip ceiling (65,532 ADU), so a reader can see that a peak between them is not clipped but is no longer linear. A ceiling or limit from a datasheet or a generic profile is stored as assumed.
- **Our result:** M 57 library stack, 2026-10-23, the 60 s group (61 frames). Three frames were read; the brightest star was clipped in all three. The sky was 800 to 840 ADU, the ceiling 65,532 ADU (the frames' own highest value), and the estimated peaks above the sky were 4.5e6, 5.3e6 and 6.5e6 ADU, which is 70, 82 and 100 times the room. The group's fraction is the median, 82. That is beyond the 80 times that [STK-41](#stk-41) checked on the ASI533MM Pro, so the fraction and the peak are stored as not measured, and only "clipped in 3 of 3 frames" is firm. The estimate depends strongly on the star width it is given. These frames carry no measured star width (`measured_fwhm_px` is empty), so the default of 3.0 px was used. The registration width of the same frames has a median of 2.47 px (x direction), and at that width the estimate is above 100 times the room and is refused; at 3.5 px it is 33 to 47 times. The old recommended exposure is not computed or stored here.
- **Verdict:** Not judged.
- **Limits:** The estimate assumes a Moffat profile of the frame's star width with beta 3, and the width is the measured one when the frame has it, otherwise the default 3.0 px, which is wider than the 1.9 to 2.3 px of good nights. The unclipped peak of a star that is sampled by about two pixels depends on where the star falls on the pixel grid: the same star's unclipped peak at two exposures differs by a median of 14% and a 90th percentile of 38% ([STK-41](#stk-41)), so a measured peak is good to about that. Three frames per group are sampled, so a star that clips in one frame only is seen through the median. The ceiling of a clipped frame is its own highest value, which equals the profile's ceiling only when the sensor clips there. A frame that gave no peak is left out of the median, and the count says how many remain. The raw frames of the D5300 are a Bayer mosaic read as one image.
- **To tune for your setup:** Check that your camera profile has the right clip ceiling and linearity limit, and the measured star width. To check the estimate on your camera, take the same field at two exposure lengths and compare the peaks as [STK-41](#stk-41) does.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_group_peak.py` and `test_record_science.py`.
- **Raw record:** None kept.

### STK-41

**Does an estimated peak of a clipped star grow with the exposure?**

- **Claim:** The peak of a clipped star, estimated from the pixels around its clipped core, grows in proportion to the exposure within 66% of the exposure ratio in at least 90% of the cases, on the ASI533MM Pro up to 80 times the room between the sky and the ceiling. It does not hold on the D5300. Proportional growth does not make the estimate right: against an unclipped star of the same field at a shorter exposure, the ASI533MM Pro's estimates are typically half the true peak and off by a factor of 2 to 5 either way, and larger ones are several times too high.
- **Settings it supports:**
  - code constant `CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA` = 80 for the ASI533MM Pro (none for any other camera) in `pipelines/stacking/post_processing/exposure_saturation.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA -->
  - code constants `CHECKED_ESTIMATE_ERROR_FRACTION` = 0.66, `CHECKED_ESTIMATE_SHARE_WITHIN_ERROR` = 0.97 and `CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS` = (0.22, 0.48, 2.7) in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_ERROR_FRACTION --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_SHARE_WITHIN_ERROR --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS -->
  - code constant `MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO` = 100 in the same file. Relation: sanity check only. It was set from nine comparisons that were recorded only in a code comment; this entry replaces them. The new data put the limit of proportional growth on the ASI533MM Pro near 80, and show no limit on the D5300. The constant is not changed. <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO -->
- **Status:** Partly measured. 35 target and camera groups with two or more exposure lengths were read, and 27 gave star pairs. The ASI533MM Pro result rests on 13 targets (10 below 80 times the room), the D5300 result on 14. Nothing is held out by night or field; the nights differ, but the same fields enter every bin.
- **Data you need:** Light frames of one field at two or more exposure lengths (the same camera and optic), with the raw files on disk.
- **Procedure:**
  1. For every target and camera, read up to three frames at each exposure length (spread through the group, as `measure_group_saturation` does).
  2. In each frame list the 25 brightest stars: a clipped star (4 or more connected pixels at the frame's highest value) gets the library's wing estimate of its peak (`_estimated_peak_from_wings`), an unclipped star its measured peak (the highest pixel whose four neighbors are 20% of the way up). A peak at the ceiling or above the linearity limit is not used as a measured peak.
  3. Match the stars of a shorter and a longer frame by the pixel shift (or a 180 degree flip) that most of them agree on, and keep a star only if it lies within 2 px of where that transform puts it and its peak in the shorter frame is at least 300 ADU.
  4. For each matched star compute the error ``E = (peak_long / peak_short) / (t_long / t_short) - 1``. Bin it by the size of the longer frame's estimate in room multiples.

  The scripts are in the session's scratch folder (`science/peak_recheck/`): `survey.py`, `recheck.py` and `analyse.py`. Repeat with `python recheck.py` (about 25 minutes), then `python analyse.py --markdown`. The library calls are `exposure_saturation.measure_frame_saturation` <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_saturation.measure_frame_saturation --> and its helpers.
- **Acceptance criterion:** A bin of estimate sizes is supported when at least 90% of its cases (and its 90th percentile error) are within 66% of the exposure ratio, for every bin up to it, with at least 4 targets in the top bin. The 66% is the criterion of the earlier nine comparisons.
- **What to look at:** The estimated-to-estimated table (proportionality) and the measured-to-estimated table (the estimate against a measured peak). The first can hide a bias that is the same at both exposures (a wrong star width, a wrong profile); only the second shows it.
- **Our result:** 2026-10-23, frames on disk, read only; 121 exposure pairs (ratio of at least 1.4) in 35 groups gave 879 frame combinations, of which 457 found no consistent shift (the field moved between nights, or too few stars). 71 exposure pairs gave 3,368 usable star pairs (2,688 estimated to estimated, 212 measured to estimated, 467 measured to measured). The star width given to the estimate was the default 3.0 px for every frame, because no frame carries a measured width.

  Proportionality, estimated to estimated (cases / targets, median error, share within 66%):

  | Estimate size (x room) | ASI533MM Pro | D5300 |
  | --- | --- | --- |
  | 1 to 2 | 94 / 5, -0.41, 95% | 1 / 1 |
  | 2 to 5 | 263 / 7, -0.18, 98% | 51 / 5, -0.63, 57% |
  | 5 to 10 | 252 / 9, -0.18, 98% | 219 / 8, -0.35, 74% |
  | 10 to 20 | 235 / 9, -0.09, 97% | 347 / 9, -0.11, 76% |
  | 20 to 40 | 92 / 7, -0.07, 90% | 290 / 10, +0.20, 70% |
  | 40 to 63 | 50 / 5, -0.05, 100% | 130 / 10, +0.22, 65% |
  | 63 to 80 | 23 / 4, +0.07, 96% | 81 / 9, +0.25, 79% |
  | 80 to 100 | 30 / 5, +0.14, 83% (90th percentile 1.5) | 61 / 10, +0.34, 62% |
  | 100 to 200 | 62 / 5, +0.45, 61% | 139 / 10, +0.07, 80% |
  | 200 to 1,000 | 58 / 3, +0.64, 53% | 124 / 11, +0.06, 65% |
  | over 1,000 | 44 / 2, +0.96, 30% | 42 / 6, +0.15, 95% |

  On the ASI533MM Pro every bin up to 80 times the room passes (1,009 cases, 10 targets, 97% within 66%, 90th percentile error 0.50), and 80 to 100 does not, so the range is 80. On the D5300 the first bin with cases already fails and no bin from 2 to 1,000 times passes, so it has no range. The earlier statement of 63 times and "nothing between 63 and 100" is replaced: 63 to 80 passes on 23 cases.

  The estimate against a measured peak, measured to estimated (cases / targets, median error, share within 66%):

  | Estimate size (x room) | ASI533MM Pro | D5300 |
  | --- | --- | --- |
  | 0 to 2 | 64 / 5, -0.60, 58% | 1 / 1 |
  | 2 to 5 | 67 / 5, -0.52, 67% | 12 / 2, +1.40, 33% |
  | 5 to 10 | 6 / 1, +1.21, 33% | 20 / 2, +3.09, 0% |
  | 10 to 20 | 10 / 2, -0.03, 0% | 1 / 1, +5.2 |
  | 20 to 63 | 3 / 2, +2.6 to +4.9 | 11 / 2, +8.60, 0% |
  | 63 to 200 | 17 / 2, +10.5 to +18, 0% | none |

  Below 80 times the room on the ASI533MM Pro (156 cases, 7 targets: Albireo, Alnath, M 27, M 42, M 81, NGC 2403, NGC 2903, mostly 1 to 5 times) the estimate divided by the measured peak scaled to the longer exposure has a median of 0.48 and a 10th to 90th percentile range of 0.22 to 2.7; the extremes are 0.09 and 13. The cases above 5 times the room are few and come from three targets, so the cases are examples, not an average. The large overestimates are M 42 (the saturated region is the nebula around the Trapezium, not a star on a flat sky) and M 81 from 30 s to 300 s; the Albireo A star from 0.05 s to 30 s is a 5-fold underestimate. They say that the estimate can be wrong by a factor of 2 to 13 at any size. Two unclipped peaks of the same star at two exposures, with nothing clipped (measured to measured, 467 cases), differ by a median of 14% and a 90th percentile of 38%, because the star falls on a different part of the pixel (the seeing of 1.9 to 2.3 px is near the sampling limit), the transparency changes and the focus drifts; that is the noise floor of the whole test.

  Every exposure pair that gave a usable star (71) is in the table below: its brightest star in the longer frames, the median error over the frame pairs and the size of the longer frame's estimate. Fifty pairs gave none: the D5300's NGC 7023 (30 to 300 s) and M 31 (10 to 300 s) pairs, M 42, M 51 and M 81 pairs, IC 1805 (300 to 600 s), the ASI533MM Pro's Albireo pairs from 0.02 s, the Navi pairs from 0.01 s, M 101, M 13 (3.2e-5 s to 30 s) and M 81 pairs 30 s to 60 s and 30 s to 600 s.

| Target | Camera | Short / long (s) | Ratio | Frame pairs | Kind | Median error | Range of error | Long estimate (x room) | Same night |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Albireo | ASI533MM Pro | 0.05 / 0.1 | 2.0 | 5 | M->M 5 | -0.15 | -0.44 to +0.30 | nan | yes |
| Albireo | ASI533MM Pro | 0.05 / 0.25 | 5.0 | 4 | M->E 3, M->M 1 | -0.86 | -0.91 to -0.13 | 0.6 | yes |
| Albireo | ASI533MM Pro | 0.05 / 30 | 600.0 | 3 | M->E 3 | -0.82 | -0.86 to -0.77 | 13.0 | yes |
| Albireo | ASI533MM Pro | 0.1 / 0.25 | 2.5 | 9 | M->M 9 | +0.41 | +0.25 to +0.79 | nan | yes |
| Albireo | ASI533MM Pro | 0.1 / 30 | 300.0 | 3 | M->E 3 | -0.74 | -0.80 to -0.73 | 13.0 | yes |
| Albireo | ASI533MM Pro | 0.25 / 30 | 120.0 | 3 | E->E 3 | +0.07 | +0.00 to +0.55 | 77.1 | yes |
| Alnath | ASI533MM Pro | 2 / 10 | 5.0 | 1 | E->E 1 | +0.42 | +0.42 to +0.42 | 273.9 | yes |
| Altair | D5300 | 300 / 600 | 2.0 | 1 | E->E 1 | +0.34 | +0.34 to +0.34 | 72295.2 | yes |
| IC 1396 | D5300 | 120 / 180 | 1.5 | 5 | E->E 5 | -0.24 | -0.54 to +0.50 | 34.8 | yes |
| M 101 | D5300 | 180 / 300 | 1.7 | 6 | E->E 6 | +0.55 | -0.51 to +2.17 | 255.1 | no |
| M 106 | D5300 | 120 / 180 | 1.5 | 3 | E->E 3 | +0.19 | +0.01 to +2.40 | 40.8 | yes |
| M 106 | D5300 | 120 / 300 | 2.5 | 3 | E->E 3 | +0.16 | -0.05 to +0.25 | 179.3 | yes |
| M 106 | D5300 | 180 / 300 | 1.7 | 1 | E->E 1 | +0.14 | +0.14 to +0.14 | 1060.9 | yes |
| M 27 | ASI533MM Pro | 30 / 60 | 2.0 | 8 | E->E 8 | +0.96 | +0.45 to +1.64 | 10.1 | no |
| M 27 | ASI533MM Pro | 30 / 120 | 4.0 | 9 | E->E 9 | +1.04 | -0.26 to +2.03 | 22.6 | yes |
| M 27 | ASI533MM Pro | 30 / 300 | 10.0 | 3 | E->E 3 | +2.12 | +1.36 to +2.29 | 85.1 | no |
| M 27 | ASI533MM Pro | 60 / 120 | 2.0 | 8 | E->E 8 | +0.10 | -0.49 to +0.52 | 22.6 | no |
| M 27 | ASI533MM Pro | 60 / 300 | 5.0 | 3 | E->E 3 | +0.18 | -0.10 to +0.65 | 35.2 | no |
| M 27 | ASI533MM Pro | 120 / 300 | 2.5 | 3 | E->E 3 | +0.35 | +0.09 to +2.19 | 85.1 | no |
| M 31 | D5300 | 10 / 20 | 2.0 | 3 | E->E 3 | +0.11 | +0.07 to +0.25 | 14.1 | yes |
| M 31 | D5300 | 30 / 120 | 4.0 | 2 | M->E 1, E->E 1 | +7.12 | -0.07 to +14.30 | 121.1 | no |
| M 31 | D5300 | 30 / 180 | 6.0 | 1 | E->E 1 | +0.27 | +0.27 to +0.27 | 425.2 | no |
| M 31 | D5300 | 30 / 300 | 10.0 | 1 | M->E 1 | -0.33 | -0.33 to -0.33 | 3.7 | no |
| M 31 | D5300 | 40 / 60 | 1.5 | 9 | E->E 9 | +0.08 | -0.10 to +0.16 | 475.8 | yes |
| M 31 | D5300 | 40 / 120 | 3.0 | 8 | E->E 8 | -0.37 | -0.68 to +0.71 | 22.4 | no |
| M 31 | D5300 | 40 / 180 | 4.5 | 3 | E->E 3 | -0.38 | -0.68 to +0.94 | 23.0 | no |
| M 31 | D5300 | 40 / 300 | 7.5 | 3 | E->E 3 | -0.89 | -0.90 to -0.89 | 220.3 | no |
| M 31 | D5300 | 60 / 120 | 2.0 | 9 | E->E 9 | -0.64 | -0.77 to -0.35 | 208.3 | no |
| M 31 | D5300 | 60 / 180 | 3.0 | 3 | E->E 3 | -0.69 | -0.69 to -0.68 | 425.2 | no |
| M 31 | D5300 | 60 / 300 | 5.0 | 3 | E->E 3 | -0.90 | -0.90 to -0.89 | 220.3 | no |
| M 31 | D5300 | 120 / 180 | 1.5 | 3 | E->E 3 | +0.05 | -0.27 to +0.36 | 425.2 | yes |
| M 31 | D5300 | 120 / 300 | 2.5 | 3 | E->E 3 | -0.67 | -0.77 to -0.58 | 220.3 | yes |
| M 31 | D5300 | 180 / 300 | 1.7 | 1 | E->E 1 | -0.69 | -0.69 to -0.69 | 220.3 | yes |
| M 42 | D5300 | 30 / 60 | 2.0 | 3 | E->E 3 | +0.10 | +0.06 to +0.27 | 1358.2 | yes |
| M 42 | D5300 | 30 / 120 | 4.0 | 3 | E->E 3 | +0.55 | +0.52 to +0.60 | 7472.6 | yes |
| M 42 | ASI533MM Pro | 30 / 300 | 10.0 | 3 | E->E 3 | +0.09 | +0.03 to +0.66 | 14357.4 | yes |
| M 45 | ASI533MM Pro | 30 / 60 | 2.0 | 1 | E->E 1 | +0.24 | +0.24 to +0.24 | 763.3 | yes |
| M 45 | ASI533MM Pro | 30 / 300 | 10.0 | 3 | E->E 3 | +1.47 | +1.23 to +2.13 | 7782.6 | yes |
| M 45 | ASI533MM Pro | 60 / 300 | 5.0 | 3 | E->E 3 | +0.99 | +0.80 to +1.51 | 7782.6 | yes |
| M 51 | D5300 | 60 / 180 | 3.0 | 8 | E->E 7, E->M 1 | +0.21 | -0.97 to +1.37 | 286.7 | yes |
| M 57 | ASI533MM Pro | 30 / 60 | 2.0 | 7 | E->E 7 | -0.06 | -0.13 to +0.16 | 80.5 | no |
| M 81 | ASI533MM Pro | 30 / 300 | 10.0 | 2 | M->E 2 | +4.25 | +3.65 to +4.85 | 41.6 | yes |
| M 81 | ASI533MM Pro | 60 / 300 | 5.0 | 3 | E->E 3 | -0.28 | -0.37 to -0.20 | 9.3 | no |
| M 81 | ASI533MM Pro | 60 / 600 | 10.0 | 6 | M->E 2, E->E 4 | -0.30 | -0.50 to -0.17 | 20.0 | no |
| M 81 | ASI533MM Pro | 300 / 600 | 2.0 | 2 | E->E 2 | +0.10 | +0.04 to +0.16 | 20.1 | yes |
| NGC 1893 | ASI533MM Pro | 60 / 300 | 5.0 | 9 | E->E 9 | +0.07 | -0.63 to +0.68 | 58.7 | yes |
| NGC 1893 | ASI533MM Pro | 60 / 600 | 10.0 | 3 | E->E 3 | +0.23 | -0.29 to +0.61 | 139.7 | yes |
| NGC 1893 | ASI533MM Pro | 300 / 600 | 2.0 | 3 | E->E 3 | +0.15 | -0.04 to +0.43 | 139.7 | yes |
| NGC 2024 | D5300 | 180 / 300 | 1.7 | 3 | E->E 3 | +0.18 | +0.13 to +0.42 | 7198.0 | yes |
| NGC 2244 | ASI533MM Pro | 300 / 600 | 2.0 | 6 | E->E 6 | +0.10 | -0.06 to +0.29 | 165.4 | yes |
| NGC 2403 | ASI533MM Pro | 30 / 60 | 2.0 | 3 | E->E 3 | -0.00 | -0.01 to +0.26 | 4.5 | yes |
| NGC 2403 | ASI533MM Pro | 30 / 300 | 10.0 | 3 | E->E 3 | +0.44 | +0.09 to +0.45 | 20.9 | yes |
| NGC 2403 | ASI533MM Pro | 60 / 300 | 5.0 | 9 | E->E 9 | -0.04 | -0.35 to +0.46 | 20.9 | yes |
| NGC 2903 | ASI533MM Pro | 300 / 600 | 2.0 | 9 | E->E 9 | -0.16 | -0.27 to +0.24 | 55.3 | yes |
| NGC 4438 | D5300 | 120 / 180 | 1.5 | 6 | E->E 6 | +0.13 | -0.19 to +0.24 | 69.7 | yes |
| NGC 4438 | D5300 | 120 / 300 | 2.5 | 4 | E->E 4 | +0.14 | -0.05 to +0.35 | 146.8 | no |
| NGC 4438 | D5300 | 180 / 300 | 1.7 | 6 | E->E 6 | +0.13 | -0.21 to +0.95 | 123.0 | no |
| NGC 4438 | ASI533MM Pro | 300 / 600 | 2.0 | 3 | E->E 3 | -0.10 | -0.24 to +0.24 | 11.1 | yes |
| NGC 6823 | D5300 | 300 / 600 | 2.0 | 2 | E->E 2 | +0.19 | -0.12 to +0.49 | 2336.5 | yes |
| NGC 6888 | D5300 | 60 / 120 | 2.0 | 3 | E->E 3 | -0.42 | -0.46 to +0.55 | 276.7 | yes |
| NGC 6888 | D5300 | 60 / 180 | 3.0 | 3 | E->E 3 | +0.43 | +0.34 to +2.83 | 1029.4 | yes |
| NGC 6888 | D5300 | 120 / 180 | 1.5 | 5 | E->E 5 | +0.08 | -0.36 to +1.47 | 1792.4 | yes |
| NGC 7000 | D5300 | 180 / 300 | 1.7 | 2 | E->E 2 | -0.04 | -0.14 to +0.06 | 623.2 | yes |
| NGC 7023 | D5300 | 60 / 120 | 2.0 | 3 | E->E 3 | +0.40 | -0.14 to +0.53 | 34.5 | yes |
| NGC 7023 | D5300 | 60 / 180 | 3.0 | 1 | E->E 1 | +2.40 | +2.40 to +2.40 | 129.9 | yes |
| NGC 7023 | D5300 | 60 / 300 | 5.0 | 1 | E->E 1 | -0.35 | -0.35 to -0.35 | 40.0 | yes |
| NGC 7023 | D5300 | 120 / 180 | 1.5 | 3 | E->E 3 | +1.43 | +1.22 to +2.44 | 129.9 | yes |
| NGC 7023 | D5300 | 120 / 300 | 2.5 | 3 | E->E 3 | -0.54 | -0.58 to -0.35 | 40.0 | yes |
| NGC 7023 | D5300 | 180 / 300 | 1.7 | 1 | E->E 1 | -0.81 | -0.81 to -0.81 | 40.0 | yes |
| Navi | ASI533MM Pro | 2 / 3 | 1.5 | 9 | E->E 9 | -0.17 | -0.33 to +0.01 | 180.2 | yes |
| Vega | D5300 | 1 / 2 | 2.0 | 9 | M->M 8, E->E 1 | +0.10 | -0.16 to +0.34 | 3.0 | yes |

- **Verdict:** Not judged. The numbers set the range of the quality rows (80 times the room on the ASI533MM Pro, none elsewhere); they do not make the estimate accurate.
- **Limits:** The stars in a pair are the same star only if the match is right; a wrong match shows as a large error, and the 2 px limit removed the worst. Faint companions, nebulosity under a clipped star (M 42, NGC 2024 with estimates of 242,000 times the room) and the Bayer mosaic of the D5300 make the estimate worse in ways this test cannot separate. Nights differ in transparency and focus (28% of the 71 exposure pairs are on different nights; they are marked), and the baseline of 14% is for unclipped stars. The measured-to-estimated cases at more than 5 times the room are few. Every frame used the default star width of 3.0 px; the estimate grows steeply as the assumed width falls (see [STK-40](#stk-40)), and a measured width would change the sizes and probably the range. The check does not test the sample of three frames per group that the quality rows use. It does not check whether the Moffat index of 3 is right.
- **To tune for your setup:** Run the scripts on your own pairs of exposure lengths. Give the estimate the star width measured in each frame, and re-run to see whether the range moves. A camera that is not listed has no range, so its estimated peaks are stored as not measured.
- **Regenerate with:** The scripts in the procedure.
- **Guarded by tests:** `test_group_peak.py` (the range by camera) and `test_record_science.py` (assumed inside the range, not measured beyond it, with its reason). The real-data run has no test.
- **Raw record:** `science/peak_recheck/pairs.jsonl` in the session's scratch folder holds every matched star pair (5,604 lines); not kept in the repository.

### STK-42

**The noise of the difference of two half stacks**

- **Claim:** The difference of two stacks made from disjoint halves of the library stack's frames, registered to one reference frame, cancels what the frames share (sky structure, faint stars, nebula) and keeps their random noise. Measured in the target's empty apertures, it recovers a known injected noise within 12% on average (single fields 17% low to 20% high) where the plain empty-aperture noise of one half reads up to 8.5 times too high. On the M 57 library stack the random noise it implies is 1.32 times the noise the frames' light predicts ([STK-38](#stk-38)), where the empty-aperture noise is 2.48 times.
- **Settings it supports:** None. The fewest frames in a half is the design estimate STK-D25.
- **Status:** Partly measured. One synthetic grid (ten cases, six stacks each), one injection of the implied-noise formulas on frames of unequal noise and weight, and one real target, one night, one camera. The real stack has no independent truth, so only its agreement with the model is known; the injections show the difference and the formulas follow the injected noise, not that the M 57 value is right.
- **Data you need:** A target with a library stack of at least 16 light frames of one setting, stacked by the current code (so the stack's apertures, noise sums, frame weights and registration reference frame are recorded), and the raw frames on disk.
- **Procedure:**
  1. Run the job (or `measure_noise_growth`, which runs it by default). Nothing starts it after a stack; a caller does. It splits the library stack's frames by capture time into the odd and the even places, puts the registration reference frame into both halves and hands it to the stacker as the frame to line up with (a half cannot choose its own; the job still refuses when a half reports another one), stacks each half (normal settings, no edge trim, no quarantine), subtracts them and measures the noise of the difference in the stack's stored apertures. Pixels that either half does not cover are left out. The halves are deleted afterwards.
  2. Read the nine rows it stores on the library stack and the result it returns.
  3. Compare the library stack's random noise that the difference implies with the model's expected noise and with the empty-aperture noise.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  astro.processing.measure_half_difference_noise("M 57")
  for name in (
      "stacking.half_difference_noise_stack_unit",
      "stacking.half_difference_noise_normalized",
      "stacking.half_difference_reference_flux_stack_unit",
      "stacking.half_difference_odd_effective_frame_count",
      "stacking.half_difference_even_effective_frame_count",
      "stacking.half_difference_shared_frame_odd_weight_fraction",
      "stacking.half_difference_shared_frame_even_weight_fraction",
      "stacking.half_difference_variance_factor",
      "stacking.half_difference_model_variance_ratio",
      "stacking.effective_frame_count",
      "stacking.empty_aperture_noise_stack_unit",
      "stacking.expected_empty_aperture_noise_stack_unit",
  ):
      for row in astro.quality.query(metric=name, target_id="M 57", library_only=True):
          print(name, row.value, row.status, row.reason)
  ```

  `ProcessingPipelines.measure_half_difference_noise` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.measure_half_difference_noise --> calls `run_half_difference_noise` <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.run_half_difference_noise -->. The split is `split_into_halves` <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.split_into_halves -->, each half is stacked by `stack_frame_subset` <!-- impl: astrometricslib.pipelines.stacking.subset_stack.stack_frame_subset --> without the edge trim, and the difference is measured by `measure_difference_noise` <!-- impl: astrometricslib.pipelines.stacking.post_processing.difference_noise.measure_difference_noise -->, which puts the stored apertures on the first half's stars and sums the difference image. The library stack's random noise that the difference implies is `full_stack_noise_equivalent` <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.full_stack_noise_equivalent --> from `difference_variance_factor` <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.difference_variance_factor -->. It is returned in the result and not stored; every part of it is. The version with each frame's own noise is `model_variance_ratio` <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.model_variance_ratio --> and `full_stack_noise_equivalent_from_model` <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.full_stack_noise_equivalent_from_model -->. The grid injection is `injection.py`, the injection of the implied-noise formulas is `implied_noise.py` and the real run `real_m57.py` in the session's scratch folder (`science/halfdiff/`).

  The formula. If every frame had the same noise sigma, a half with weight shares a (adding up to 1) has the variance sigma^2 x sum(a^2) = sigma^2 / N_eff, and the difference of the odd half (shares a) and the even half (shares b) has sigma^2 x F, with F = sum((a - b)^2) over all frames (a frame in one half only has 0 in the other). The library stack has sigma^2 / N_eff,library. So its random noise is the difference divided by sqrt(N_eff,library x F). For two disjoint halves of N equal frames F = 2 / N and N_eff,library = 2N, so the factor is 1 / 2. A frame in both halves lowers F by twice the product of its two shares. Frames differ in noise and the star-width weights are not noise weights, so this is an estimate. The second version lets frames differ: with the frame's variance v = (sky electrons + read noise squared) / gain squared in ADU squared, from its stored sky rate, exposure and camera profile (the model of [STK-38](#stk-38)), R = sum((a - b)^2 v) / sum(s^2 v), where s is the frame's share in the library stack, and the library stack's random noise is the difference divided by sqrt(R). With equal v, R = N_eff,library x F. Both are stored as parts: the two shares of the shared frame (so F = 1 / N_eff,odd + 1 / N_eff,even - 2 a b can be rebuilt from the counts), F, R, and the reference flux that the normalized difference divides by.
  The job also stores `stacking.half_difference_aperture_set_number` (definition version 1) on the difference rows, in the same encoding as `stacking.aperture_set_number` in [STK-36](#stk-36). It records which aperture set measured the difference, so that the forecast check can compare it only with a stack that used the same set. When the apertures cannot be read, the row is stored as missing with the reason. It is a tenth row beside the nine listed above. Status: design estimate; unit tests only.

- **Acceptance criterion:** Derived from the errors stated here, before the numbers are read against it. The grid injection reads each case as the ratio of the mean difference to sqrt(2) times a truth. Each estimate of a spread from n apertures has a relative error of 1.1664 / sqrt(n) (the factor in `MAD_SIGMA_ERROR_FACTOR`). A field has 100 apertures and three realizations, so its mean difference has an error of 1.1664 / sqrt(300) = 6.7%; the truth pools 300 sums, also 6.7%; the ratio of a field has 9.5%, and the mean of the two fields of a case 6.7%. The criterion is two standard errors: the mean of each case within 13.5% of 1, and a single field within 19%. Two runs on the same frames agree within 1%. For the implied noise of the formulas (the second injection, below), a spread of M pixels has a relative error of 1 / sqrt(2 M), so the ratio of two such spreads from the same noise images has an error of at most 2 / sqrt(2 M); the criterion is three times that (0.67% for 400,000 pixels). The earlier version of this entry used 15%, "set at the first run", which is not a derived bound; this one replaces it.
- **What to look at:** The difference noise next to the empty-aperture noise and the expected noise, all in stack units or all normalized. The empty-aperture noise holds what the frames share; the difference does not. The difference keeps the noise that registration adds: each frame is resampled on its own, so the correlation between neighboring pixels is in both halves and does not cancel, and the model leaves it out. What cancels is everything the halves hold in common, including the calibration frames' noise (the master dark, bias and flat are the same in both; the master flat's random noise is 4.8% of the measured noise on M 57, [STK-39](#stk-39)). A gap between the model and the difference is random noise the model leaves out (correlated pixel noise from registration); a larger gap between the difference and the empty-aperture noise is structure the frames share.
- **Our result:** Injection, 2026-10-23: 900 by 900 pixel stacks with bright stars to place the apertures, shared structure (300 faint stars and a smooth texture of 3 px scale, with an amplitude of 0 to 1.6 times one half's pixel noise) and noise fresh for each half, white or correlated over 0.8 px. For each of ten cases, two structure seeds, the apertures were chosen on a library stack by the current code (faint-source mask on), and three realizations of two halves were measured. The truth is the empty-aperture noise of structure-free stacks with the same noise in the same apertures (pooled over three realizations, about 300 sums). Ratios to the truth, as mean (lowest to highest of the two fields):

  | Noise | Structure amplitude | One half's empty-aperture noise / truth | Difference / (sqrt(2) x truth) |
  | --- | --- | --- | --- |
  | white | 0 | 1.09 (1.05 to 1.12) | 1.03 (1.00 to 1.05) |
  | white | 0.2 | 1.44 (1.43 to 1.45) | 0.96 (0.95 to 0.96) |
  | white | 0.4 | 2.30 (2.10 to 2.51) | 0.95 (0.94 to 0.96) |
  | white | 0.8 | 3.65 (3.30 to 3.99) | 0.90 (0.87 to 0.93) |
  | white | 1.6 | 8.48 (7.57 to 9.39) | 1.07 (1.06 to 1.08) |
  | correlated | 0 | 1.00 (0.92 to 1.09) | 0.99 (0.95 to 1.04) |
  | correlated | 0.2 | 0.93 (0.91 to 0.95) | 0.89 (0.83 to 0.95) |
  | correlated | 0.4 | 1.42 (1.42 to 1.43) | 1.12 (1.04 to 1.20) |
  | correlated | 0.8 | 1.94 (1.92 to 1.97) | 1.03 (1.00 to 1.07) |
  | correlated | 1.6 | 3.48 (3.14 to 3.83) | 0.97 (0.94 to 1.01) |

  The difference follows the injected noise (0.89 to 1.12 on the mean, 0.83 to 1.20 for one field) at every structure amplitude, and the correlated noise is kept, while the plain noise rises with the structure to 8.5 times the truth. The spread of the difference's ratios (a few percent) is the sampling error of 100 apertures and of the truth, not a bias that grows with the structure.

  The grid above was read against two bounds that were not derived. Against the derived bound (above), the case means of 0.89 (correlated, amplitude 0.2) and 1.12 (correlated, 0.4) are 11% and 12% from 1, inside the 13.5% limit, but only just (1.6 and 1.8 standard errors); every other case mean is inside by more. One single field is outside its 19%: 1.20 (correlated, 0.4, +20%). Ten cases of two fields give 20 field values, of which one outside a two-standard-error limit is expected by chance (about one in twenty). The grid was not rerun for the derived bound.

  The implied-noise formulas, tested on their own (2026-10-23, `implied_noise.py` and the test `test_the_implied_noise_follows_the_formulas_on_unequal_frames`): 40 frames of independent white noise, 400,000 pixels each, a sky from 80 to 400 electrons through the night (so the frames' variances differ by a factor of 4.4, with 3.8 electrons of read noise and a gain of 0.783), weights from 0.1 to 1 that do not follow the noise, one frame (number 17) in both halves, the odd and the even places as the halves. The truth is the standard deviation of the weighted mean of all frames. Over five seeds the equal-noise value D / sqrt(N_eff,library x F) was 0.998 of the truth on average (0.989 to 1.005) and the value with each frame's variance, D / sqrt(R), was 1.001 (0.999 to 1.005). With weights that rise with the frame's noise (the worst case for the equal-noise formula) they were 1.004 (1.000 to 1.008) and 1.001 (0.998 to 1.005). One standard deviation of 400,000 pixels has an error of 0.11%, so the derived bound for the ratio is 0.67%. The model value is inside it in all ten runs (largest deviation 0.5%). The equal-noise value is outside it in two (0.989 in the first set, 1.008 in the stress set), so the model version is the better one, though both are within 1.1% of the truth. The committed test asserts the model value inside the derived bound and the equal-noise value within 2% (a looser limit chosen for the test, not derived). The alternating split puts frames of every sky in both halves, so the two formulas agree to about 1% here. Halves that differ systematically in noise would separate them; this was not tested.

  Real data, 2026-10-23 (rerun after the board's fixes), the M 57 library stack (night of 2026-10-03, 61 frames of 60 s, ZWO ASI 533MM Pro, effective frame count 40.78), on scratch copies; the copy's own aperture file predates the current version, so the apertures were chosen again on a private copy of the library stack (100 apertures, identity `v3-63ea724613d2`) and the job ran on that copy. The odd half held 32 frames and the even half 30, both with `M_57_Light_Luminance_050.fits`, the frame the library stack was registered to. The reference was handed to the stacker for both halves (`setref`), and Siril reported it as the reference of both. The values, in the stack's units (one ADU is 1/65535):

  | Quantity | Value | Against the model's expected 4.30e-4 |
  | --- | --- | --- |
  | Empty-aperture noise of the library stack | 1.068e-3 (+-1.2e-4) | 2.48 |
  | Noise of the difference of the halves | 1.054e-3 (+-1.2e-4) | not comparable (it holds two halves) |
  | Library stack's random noise implied, equal-noise F | 5.68e-4 | 1.32 |
  | Library stack's random noise implied, per-frame model R | 5.68e-4 | 1.32 |
  | Empty-aperture noise / the implied random noise | 1.88 | |

  The halves' effective frame counts were 18.49 and 18.63. The variance factor F was 0.08449, against 0.1082 (1 / 18.49 + 1 / 18.63) without the shared frame, whose weight shares in the two halves were 0.1075 and 0.1083 (1 / 18.49 + 1 / 18.63 - 2 x 0.1075 x 0.1083 = 0.08449, so the stored parts rebuild F). The variance ratio with each frame's own modeled noise, R, was 3.442, against N_eff,library x F = 3.445 (the frames' modeled variances, from their stored sky rates, differ little through this night, so the two implied noises agree to 0.05%: 9.131e-5 and 9.136e-5 normalized). The normalized difference was 1.695e-4 (+-2.0e-5), against 1.714e-4 (+-2.0e-5) for the library stack's empty-aperture noise. The reference flux it divides by, stored as `stacking.half_difference_reference_flux_stack_unit`, was 6.216, against 6.231 for the library stack (the ratio 0.998 shows that Siril put the half on the library stack's flux scale). Two runs on the same frames gave identical values (a ratio of 1.000000 for the noise and for the implied noise; the second stored no new rows because they were the same). The halves were deleted afterwards (the first run kept them, the second removed them).

  So about half of the empty-aperture noise of the M 57 stack (1.88 times the implied random noise) is structure the frames share, which the mask for faint sources ([STK-36](#stk-36)) did not find, and the random noise itself sits 1.32 times above the white-noise model. That excess is consistent with correlated pixel noise from the registration (at most a factor of about 1.3 in the separating tests of [STK-38](#stk-38)), but the model and the difference share the assumption that frames are equally noisy, so this is agreement, not proof.

  Cost, on the copy: 55 s for the first run and 67 s for the second, for the two half stacks (32 and 30 frames), the difference and the measurement, against 69 s for the library stack of 61 frames. Siril's temporary disk use was not measured; the M 106 run noted in `drivers/siril_interface.py` measured 65 GB for 238 frames (0.27 GB a frame), so a half of 30 frames needs about 8 GB, which Siril frees after each half. The halves are deleted afterwards, so the job leaves nothing in the library.
- **Verdict:** Not judged.
- **Limits:** One target and one night; the injection is one synthetic field of 900 by 900 pixels. The difference cancels only structure that is the same in both halves. A change that differs between them (a sky gradient that moves, seeing that changes, a moving object) stays in it; the alternating split puts a slow change in both halves, and the mask keeps the apertures 13 px from the stars, so the stars' own residuals are mostly out. The shared frame is in both halves, so the halves are not fully independent, which F accounts for under equal frame noise. The implied noise from the variance factor F assumes equal noise in every frame; the version with R lets the frames differ in sky and read noise but uses the model of [STK-38](#stk-38), so it is only as good as that model, and it was tested on synthetic frames of unequal sky only, not on frames of different length. The weights of a half are Siril's star-width weights rebuilt, and are not stored as measurements when they cannot be rebuilt (then the counts, the factor and the implied noise are missing, and the noise rows remain). It does not include noise that the frames share (the noise of the master dark, bias and flat is the same in both halves and cancels). A target with a library stack of an older code version, or without the recorded reference frame or noise sums, is not measured, with the reason; no other number takes the place.
- **To tune for your setup:** Run the job on a target with a deep or crowded field and read the difference next to the empty-aperture noise. If the difference is close to the model and the empty-aperture noise is far above it, the excess is shared structure. If they are close to each other and both above the model, the excess is random noise the model leaves out.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/test_half_difference_noise.py` checks the split (disjoint, alternating in time, only the reference frame shared, odd counts), the formulas (half the difference for equal halves, the shared frame's effect, the variance ratio R, no value from a missing weight), a synthetic field in which the difference recovers the injected noise where the plain noise does not, the injection of the implied-noise formulas on frames of unequal noise and weight, the nine rows on the library stack's subject (with the reference flux and the shared frame's two shares, from which F is rebuilt), that the library stack's reference is handed to both halves (a stacker that would pick its own first frame is told which one to use, and a stacker that ignores it is refused), the deletion of the halves after success and after a stacker failure, that a running library stack of the target stops the job with a `ConflictError`, the zero-edge mask (an aperture or ring holding an uncovered pixel gives no sum, in either half), and each refusal (spectral, too few frames, no recorded reference, no noise sums or apertures, other apertures, halves on different references, a stacker failure) storing not-measured rows with a reason. `astrometricslib/api/test/test_subset_stack_api.py` checks the job through `ProcessingPipelines` (with and without a job record, and inside the growth job), `astrometricslib/drivers/test/test_siril_frame_filter_placement.py` checks the `setref` command, and `backend/tests/test_quality_measurement_service.py` the RPCs. The real-data run has no test.
- **Raw record:** None kept in the repository. The scripts and their output are in the session's scratch folder (`science/halfdiff/`); the rows were stored in the quality store of the copy.

### STK-43

**Rotation of each frame about the registration reference**

- **Claim:** The rotation angle of the polar decomposition of the transform Siril fits for each frame, read from its registration table, agrees with an angle fitted independently from the matched stars to 0.0005 degrees (rms), and the angle drifts steadily through a night. Whether the drift tracks the polar-alignment error is not known.
- **Settings it supports:** None. This entry sets no tuned value.
- **Status:** Partly measured. One night, 61 frames; the one known polar-alignment error is that same night, which is not enough to check a conversion.
- **Data you need:** A stacking run in which Siril registered the frames with its transform (not a sequence already registered), and for the conversion, nights with a known polar-alignment error and the direction of the error.
- **Procedure:**
  1. Stack the frames. The record step stores a row for each frame: `stacking.frame_registration_rotation_deg`, in the frame's own subject.
  2. Read the rows with the frame times (the `timestamp` of each frame record, joined by the subject id), fit a straight line, and compare the angle with the one fitted from the stars.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  rows = astro.quality.query(metric="stacking.frame_registration_rotation_deg", target_id="M 57")
  for row in rows:
      print(row.subject.subject_id, row.subject.observing_night_id, row.value, row.status.value)
  ```

  `field_rotation_measurements` <!-- impl: astrometricslib.pipelines.stacking.post_processing.field_rotation.field_rotation_measurements --> builds the rows from the registration table that `parse_seq_file` <!-- impl: astrometricslib.drivers.siril_output_parsing.parse_seq_file --> reads, and `rotation_from_homography` <!-- impl: astrometricslib.pipelines.stacking.post_processing.field_rotation.rotation_from_homography --> takes the angle out of each transform. `record_stacking_quality` stores the rows in their own call, after the stack's rows, so a failure while building or storing them cannot undo the stack's rows.
- **Acceptance criterion:** Not recorded. A conversion to a polar-alignment error needs several nights with known errors and directions.
- **What to look at:** The rows against time within a night: a steady slope is a drift of the field. A jump is a meridian flip or a re-centering. The reference frame reads 0 by construction.
- **Our result:** 2026-10-23, M 57, night of 2026-10-03 to 04, 61 frames over 1.20 hours (ASI533MM Pro), registered by Siril to frame 39 of the sequence. The angle ran from -0.0075 to +0.0092 degrees. A straight line against the frame times (DATE-OBS) gave +0.01221 degrees per hour (residual rms 0.00122 degrees); the angle rose from the first frame to the last without a jump. The scale ran from 0.99996 to 1.00017.

  Which angle: the first version of this entry used `atan2(h10, h00)`; the stored angle is now the rotation of the polar decomposition of the 2 x 2 part, `atan2(h10 - h01, h00 + h11)`, which a small shear in the fit does not move. On these frames the two differ by a mean of -0.00015 degrees, an rms of 0.00057 degrees and at most 0.0018 degrees (the transforms' off-diagonal sum is under 3e-5). The slope was +0.01153 degrees per hour with `atan2(h10, h00)`.

  An independent angle: the reference frame's 60 brightest stars were moved onto each frame by Siril's transform, matched to the detected stars (all 60 matched in the frames tested), and the rotation that best turns the frame's stars into the reference's was fitted. Against the polar angle the difference had a mean of -0.00001 degrees, an rms of 0.00050 degrees and a largest size of 0.0018 degrees (the `atan2(h10, h00)` angle gave 0.00067 and 0.0021); the formal error of the star-based angle had a median of 0.00054 degrees. Its straight-line slope was +0.01191 degrees per hour. The drift (0.017 degrees over the night) is therefore about 30 times the precision of one angle.

  Sign: the angle is `atan2(h10, h00)` of the transform that moves the frame onto the reference, in Siril's coordinates (x along the columns, y counted up from the last row of the array: y = height - 1 - row). It was confirmed on these frames: with that axis and the inverse transform, all 60 reference stars land within 2.5 px of a detected star in every frame tested. A positive angle turns the frame's x axis toward +y, which is counterclockwise when the array is drawn with its first row at the top. The frame's orientation relative to the reference is the opposite sign.

  The one polar-alignment error the observatory holds is 34 arcminutes 25 arcseconds, from the Ekos log of 2026-10-03, the same night. The data point is recorded here beside the drift: +0.0115 degrees per hour at the declination of M 57 (33 degrees), against a polar-alignment error of 0.574 degrees. No conversion is made (it belongs to `wayfindinglib`), and one night with no direction for the error is insufficient evidence.

  The sign and the wrap. The angle lies in (-180, 180] degrees. A meridian flip turns the field by about 180 degrees, so the stored series jumps by about 360 degrees at the wrap; a reader unwraps it before fitting a drift. The night above has no flip.

  What a row says about itself. The reason of a measured row names the registration reference frame and the image parity (the sign of the determinant of the CD matrix of the stack's plate solution, or unknown when the stack header has none; the scratch stack's is unknown). The angle depends on which frame is the reference, so values compare only within one run (`run_id`): a restack that picks another reference changes them. The frame's time is the frame header's DATE-OBS (DATE when there is none), the start of the exposure by the FITS convention; the row holds no time and the reader joins it by the subject id.

  To repeat: `science/s3/evidence_rotation_polar.py` and `science/s3/real_rotation_polar.py` in the session's scratch folder, or the call in the procedure.
- **Verdict:** Not judged.
- **Limits:** One night and one camera. The row holds no uncertainty (Siril saves none); the precision above is from this night only. A sequence already registered, a spectral stack (shift only), a frame Siril dropped or never read, and a transform that mirrors the image give not-measured rows with the reason. A restack with the same frames gives a new row, so a reader takes the latest run, and compares angles only within one run.  The frame's time is not in the row.
- **To tune for your setup:** None.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_frame_rotation.py` checks the rows, the polar angle against a stretch, the wrap, the parity, the not-measured cases (including a frame missing from Siril's table), the shared frame subject and that `record_stacking_quality` stores the rows in their own call.
- **Raw record:** None kept.

### STK-44

**Registration step and pointing drift of a run**

- **Claim:** The distance between the pointings of two exposures taken one after the other, read from the transform Siril fits for each frame (the transform's shift at the middle of the image), separates the ZWO runs with a median step of 1.2 to 2.5 px (M 27, NGC 2244 on 2026-01-17, NGC 2903, NGC 4438, Mirach), consistent with a dither at nearly every exposure, from the rest (0.25 to 0.71 px). A small median does not mean a run was not dithered. Three runs with medians of 0.31 to 0.50 px (M 57, M 52 and NGC 7331) were dithered once every 10 exposures: the median step is the drift step, and the dither shows only in the jump count (`stacking.registration_step_jump_count`) and, independently, in the capture times, where each jump comes with an exposure gap about 40 s longer than usual. A median step below one pixel is a slow drift plus the pointing jitter between exposures and the noise of Siril's fit. Whether a given step is enough to average a sensor pattern out is not known.
- **Settings it supports:** None. The 50 px limit above which a step is left out as degenerate (`MAXIMUM_PLAUSIBLE_STEP_PX`), the 10% share of degenerate pairs and the 50% share of identity transforms above which the step rows are not measured (`MAXIMUM_DEGENERATE_SHARE`, `MAXIMUM_IDENTITY_SHARE`), and the 5 robust deviations that make a step a jump (`STEP_JUMP_SIGMAS`) are reasoned choices with the survey below behind them, not tuned values that change a result.
- **Status:** Partly measured. The stored shifts of 40 targets (those with five or more frames of one camera) were read with the production code, and the production code was run on the registration of the M 57 half stacks. A dither every 10 exposures was checked against the capture times in three runs. No second, independent measure of the pointing exists (the Ekos logs that exist on disk, 17 analyze files from 2023 and 2024, are from the Nikon era, hold no dither event and cover none of the ZWO nights), and no field has a dithered and an undithered night on the same night.
- **Data you need:** Frames registered by Siril with its transform (not a sequence already registered, and not a stack that joins several exposure groups), with capture times in the frame records. For the comparison of dithered with undithered runs: two runs of one field on one night, one with dithering and one without. The observatory holds none (see the result).
- **Procedure:**
  1. Stack the frames. The record step reads Siril's registration once into a registration record (`registration_record.py`) and stores eight rows on the stack: `stacking.registration_step_median_px`, `stacking.registration_step_mad_px`, `stacking.registration_step_jump_count`, `stacking.registration_step_degenerate_count`, `stacking.registration_step_after_drift_median_px`, `stacking.registration_drift_rate_px_per_hour`, `stacking.registration_drift_direction_deg` and `stacking.registration_drift_extent_px`. The number of pairs used is the sample count of the median row. Each measured row names, in its reason, whether the pointing was taken at the middle of the image or at the corner (the image size was unknown), the number of frames, and the pairs used and left out. A spectral stack is registered by shift only; its rows are made the same way and say so.
  2. Read the rows.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for name in (
      "stacking.registration_step_median_px",
      "stacking.registration_step_mad_px",
      "stacking.registration_step_jump_count",
      "stacking.registration_step_degenerate_count",
      "stacking.registration_step_after_drift_median_px",
      "stacking.registration_drift_rate_px_per_hour",
      "stacking.registration_drift_direction_deg",
      "stacking.registration_drift_extent_px",
  ):
      for row in astro.quality.query(metric=name, target_id="M 57"):
          print(name, row.value, row.uncertainty, row.sample_count, row.status.value, row.reason)
  ```

  `pointing_measurements` <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.pointing_measurements --> builds the rows from the registration record of the run. `shifts_from_registration` <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.shifts_from_registration --> takes each frame's pointing from its transform, `fit_nights` <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.fit_nights --> fits each night once, `step_summary` <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.step_summary --> gives the steps, and `drift_summary` <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.drift_summary --> the drift. `frame_geometry_measurements` <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_quality.frame_geometry_measurements --> reads the registration once and builds both the frames' rotation and these rows from it; `record_stacking_quality` stores the rotation rows and the pointing rows in a call each, after the stack's rows, so a failure in one leaves the others. The stored shifts of every run in the library database are listed by `evidence_steps_v2.py` in the session's scratch folder (`science/s4/`).

  Definitions. The pointing of a frame is where its transform puts the middle of the image, in pixels, compared with the registration reference frame. (Siril's matrix writes the shift of the image corner; with a turn of 0.01 degree the corner moves 0.26 px more than the middle, and that part is no pointing change.) The step is the distance between the pointings of neighboring exposures of one observing night. A step across two nights, and a step longer than 50 px, are left out and counted. The step after the drift is taken out uses a straight line fitted to each night's pointings (the Theil-Sen line decides which frames are more than 50 px off it, then a least-squares line is fitted to the rest; a night of more than 448 frames uses a fixed random sample of 100,000 pairs for the slope). The drift rate is the length of that line's slope; the axis is its direction (0 to 180 degrees from the column axis toward the row axis, rows counted downward), found only on nights whose drift is at least 3 times the error of its slope, and only when the nights' axes agree (the length of the mean at twice the angle is at least 0.5). The length of a slope is never below zero, so a rate within 3 errors of zero is biased upward (about 1.25 errors for no drift); its row says so.

  The jump count. A step is a jump when it is larger than the median step plus 5 robust deviations of the steps (1.4826 times the median absolute deviation, but at least 0.1 px, so a pure drift with a spread near zero does not count every step above its median). The 90th percentile of the steps was tried first and does not see a dither every 10 exposures: one step in ten is at its edge. A simulation of 200 runs of 60 frames of independent jitter of 0.3 px plus a drift gave 2 false jumps in 11,800 steps (about one run in a hundred, because the spread is taken from the run's own steps). The number of used pairs divided by the jump count is about the dither interval N.

  When the steps are refused. The step rows (all but the count of degenerate pairs, which stays as the evidence) are not measured when more than 10% of the pairs of one night have a step longer than 50 px, or when more than half of the frames have exactly the identity transform. A stack that joins several exposure groups has no one list of transforms (each group has its own reference) and all its rotation and pointing rows say so.
- **Acceptance criterion:** Not recorded. A threshold that calls a run dithered would need runs whose dithering is known from the capture log, and the observatory has none.
- **What to look at:** The jump count next to the median step. A median below one pixel with no jumps is pointing jitter and drift without a dither. A median below one pixel with jumps every N frames is a dither every N frames, and the capture times (a gap longer than the exposure cadence at each jump) confirm it. A median above one pixel was dithered at nearly every frame or was disturbed (a re-center, a flip, a guiding upset); the spread (`mad`) and the count of left-out pairs show which. A single jump is an upset, not a dither.
- **Our result:** 2026-10-23 and 24, the library database copy, every run with stored registration shifts and capture times (one stack per target and camera, and each observing night alone; `science/s4/evidence_steps_v2.py`). `Jumps` is the jump count over the pairs used. `After` is the median step after each night's drift line is taken out.

  | Run (ZWO ASI 533MM Pro) | Frames | Median step (px) | MAD (px) | Jumps / pairs | After drift (px) | Drift (px per hour) | Axis (deg) |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | M 57 2026-10-03 (stored shifts) | 61 | 0.45 | 0.22 | 8 / 60 | 0.44 | 5.7 | 43.4 |
  | M 52 | 52 | 0.31 | 0.15 | 5 / 50 | 0.34 | 4.4 | 89.5 |
  | NGC 7331 | 123 | 0.50 | 0.27 | 14 / 120 | 0.47 | 5.5 | 27.1 |
  | NGC 1893 | 49 | 0.35 | 0.19 | 4 / 48 | 0.26 | 7.8 | 151.0 |
  | M 81 2026-01-16 | 30 | 0.25 | 0.07 | 2 / 29 | 0.20 | 6.6 | 90.4 |
  | M 81 2026-01-14 | 9 | 0.33 | 0.05 | 0 / 8 | 0.20 | 3.4 | 149.8 |
  | M 101 2026-05-01 | 9 | 0.29 | 0.09 | 0 / 8 | 0.28 | 1.8 | 114.5 |
  | M 45 2026-01-14 | 14 | 0.65 | 0.14 | 0 / 13 | 0.32 | 7.1 | 163.7 |
  | NGC 2244 2026-01-14 | 6 | 0.38 | 0.08 | 0 / 5 | 0.41 | 4.9 | 144.4 |
  | NGC 2244 2026-01-17 | 18 | 1.28 | 0.48 | 2 / 17 | 0.77 | 12.7 | 145.9 |
  | M 27 2026-09-23 | 30 | 1.48 | 0.62 | 4 / 28 | 1.20 | 64.8 | 132.3 |
  | M 27 2026-09-24 | 33 | 2.47 | 0.96 | 2 / 32 | 1.79 | 120.6 | 134.9 |
  | NGC 2903 | 17 | 1.26 | 0.39 | 1 / 16 | 0.81 | 10.6 | 134.7 |
  | NGC 4438 | 11 | 1.27 | 0.10 | 1 / 10 | 0.64 | 11.0 | 139.6 |
  | Mirach | 40 | 1.23 | 0.70 | 0 / 39 | 1.30 | 48.5 | 90.4 |
  | NGC 2403 | 35 | 0.60 | 0.40 | 2 / 34 | 0.52 | 8.6 | 5.0 |
  | Deneb, Schedar, Mirfak, Albireo, Vega, M 13 2026-05-23 | 20 to 46 | 0.42 to 0.71 | 0.16 to 0.35 | 0 to 2 | 0.40 to 0.67 | 6 to 70 | |
  | M 42, Navi, M 101 (all nights) | 13 to 80 | 0.34 to 0.36 | 0.15 to 0.20 | 0 to 1 | 0.28 to 0.36 | 2 to 18 | |

  The median step separates the runs with a median of 1.2 px or more (consistent with a dither at nearly every exposure) from the others. It does not separate the others into dithered and undithered. In three runs the jump count and the capture times agree on a dither every 10 exposures: in NGC 7331 (frames 11, 21, 31 and 41, with exposure gaps of 158 to 173 s against the cadence of 123 s), M 52 (frames 16, 26, 36 and 46, gaps of 159 to 163 s against 123 s) and M 57 (below). In the other runs the jumps are single (a re-center, a flip or a guiding upset: NGC 1893, whose five jumps include an out-and-back excursion of 12.8 px at frames 25 and 26, and the several jumps of NGC 7331 away from its 10-frame pattern) or absent. Whether the dither is Ekos's own setting could not be checked: no Ekos log of a ZWO night is on disk.

  The Moon (median 1.34 px) is moving on the sky and is not a dither. The Nikon DSLR groups are not comparable. Of the 19 groups with enough frames, 5 are refused over all their nights: NGC 1499 (60 of its 81 frames have exactly the identity transform) and four with a large share of degenerate pairs (M 31 25%, IC 1805 18%, NGC 6823 18% and Algol 44%). Single nights are refused too: six of M 31's, all three of IC 1805's, NGC 7000 on 2023-12-13 and NGC 7023 on 2023-10-14. The other groups keep their step rows (for example NGC 7000 with 6% degenerate pairs over its two nights and NGC 7023 with 2%), with medians from 0.3 to 9 px that for most follow the drift of an unguided mount. The limit of 10% is a coin toss near itself: NGC 7000 on 2023-12-13 has 5 degenerate pairs of 49 (10.2%) and is refused, and the Moon's 9.6% passes. Among the ZWO nights only M 27 on 2026-10-02 (3 of 18 pairs, a night for which Siril named no single reference) and the six-frame M 13 night of 2026-01-10 (1 of 5) are refused. The first survey found shifts of 5,000 to 7,400 px in some DSLR groups.

  Across-night candidates of one field (they are confounded by every other difference between the nights, and none has both nights long enough for the half-stack pattern measurement of [STK-45](#stk-45), which needs eight frames in a half): M 81 (0.25 and 0.33 px on 2026-01-14 and 16, 1.32 px on 2026-01-17, 6 frames), NGC 2244 (0.38 px, 6 frames, against 1.28 px, 18 frames) and M 101 (0.29 px on 2026-05-01; the night of 2026-01-10 has 4 frames, too few for a step). **There is no pair of a dithered and an undithered run of one field on one night.**

  M 57 on the night of 2026-10-03, with the production code on the registration of its two half stacks (the run behind STK-45; the reference frame is M_57_Light_Luminance_050, the frame of the heaviest weight): 61 frames over 1.20 hours; the median step 0.455 px (spread 0.168, none left out), 5 jumps in 60 pairs (the threshold is 1.70 px), the step after the drift 0.452 px, and the drift 12.9 +- 0.6 px per hour along 37.6 degrees with the pointings ranging over 12.7 px along that axis. The jumps are frames 11, 21, 31, 41 and 51 (steps of 3.8, 4.3, 5.8, 2.7 and 3.1 px, each after an exposure gap of 99 to 174 s against the cadence of 63 s) and a 1.08 px step at frame 22: this night was dithered every 10 exposures, which its median step cannot show. The frame-to-frame jitter (0.45 px, about 0.27 px in each coordinate) is as large as the median step. The stored shifts of the library stack's own registration give a different picture of the same night: the same median (0.45 px) but 8 jumps in 60 pairs (single-frame excursions at frames 3 and 4 and a jump at frame 16 that the half-stack registration does not have), a drift of 5.7 px per hour along 43.4 degrees, and a range of 11.9 px; the registration of the library stack and of the half stacks (a different reference frame, and halves registered apart) differ by more than the fit noise in the slope, which is not understood. The pattern measurement of STK-45 uses the half stacks' registration.

  The stored shifts the survey used (`registrationDxPx`, `registrationDyPx` in the frame records) are the corner shifts. For the steps this makes no difference (the turn changes by about 1e-4 degree between neighboring frames, 2e-4 px at the corner), and the table above agrees with the first survey to two decimals where the two overlap.

  To repeat: `science/s4/evidence_steps_v2.py` (the stored shifts of every run), `science/s4/step_sequence.py` (the step and the exposure gap of each pair of one run, for example `"M 57" 2026-10-03 ZWO`) and `science/s4/real_m57_job_v2.py` (M 57 from the half stacks' registration, with the production rows) in the session's scratch folder, or the call in the procedure.
- **Verdict:** Not judged.
- **Limits:** The pointing comes from Siril's fit; it has no stated error, and the jitter and the fit noise cannot be told apart. A step measures a distance, not a cause: a dither, a re-center and a guiding upset look alike, and the capture-time gap is the only second cue. A single jump is not told from an upset; a dither is recognized by three or more jumps at regular intervals. The drift line is straight; a curved drift, or dithers that walk the pointing (M 57 moves in steps of 3 to 6 px that do not average out), leave their steps in the line's residuals and widen the rate's error; the rate and the extent of a dithered run therefore include the dither. The rate is the length of a slope and is biased upward when it is not several times its error. The axis needs one night with a drift of 3 errors; runs dithered at nearly every frame have no axis, by design. A sequence already registered, a frame Siril dropped or never read, a transform that does not map the image to the plane, a stack of several exposure groups and a run whose transforms are not pointings give not-measured rows with the reason. The rules that refuse a run are a reasoned limit near which a decision is a coin toss. The pointing rows of a spectral stack (registered by shift only) are tested on synthetic records and have not been run on a real spectral stack. A stack made before the registration record existed has none and its rows are made at the next stacking run: the backfill script does not fill them (a stored summary holds no registration and a backfill invents no run).
- **To tune for your setup:** None.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_registration_shifts.py` checks the pointing at the middle of the image (a turn does not move it), the sign of the row shift, the dropped and untimed frames, the step of a drift and of a dither, a dither every 5 and every 12 frames in the jump count (and that the 90th percentile misses the second), no jumps for jitter and drift, the step after the drift, a degenerate step, the refusal of a run with many degenerate pairs and of one with mostly identity transforms (with the count kept), a step across two nights, the axis of a drift (and its independence of sign), a drift lost in noise and its warning, two nights along different axes and along one axis, a bad transform that must not tilt the line, the fits made once, the bounded sample of pairs for a long night, the eight rows with their reasons, the rows of a spectral stack, and a missing transform. `astrometricslib/pipelines/stacking/test/post_processing/test_frame_rotation.py` checks that the registration is read once for both row groups, that a failure in one group leaves the other, and the reason of a multi-group stack.
- **Raw record:** None kept in the repository. The scripts and their output are in the session's scratch folder (`science/s4/`).

### STK-45

**What two half stacks share at a few pixels, and whether a drift smears it**

- **Claim:** The covariance of the two half stacks' fine-scale residuals (Gaussian high-pass, sigma 3 px) in sky clear of flagged sources, less the registration reference frame's own random noise, is the variance of what the halves share at that scale. It is the part of any fixed pattern that stays coherent in both halves, plus faint sky structure that the mask does not flag; it is not a measurement of the sensor pattern, and it is not an upper limit of it either (a dither that gives every exposure its own pointing takes pattern out of the shared part). A sensor pattern is smeared along the pointing drift and sky structure is not, so the halves' correlation at a lag of 6 px along the drift minus the same across it separates the two. On a real stack (M 57) an injected white pixel-scale pattern, moved by each frame's real pointing, is recovered in variance within 13% (up to a variance equal to the stack's random variance) and is seen at 5 standard errors when it is that large. The M 57 halves show no white, pixel-scale pattern that moves with the pointing (excess -0.0006 +- 0.0015), at a sensitivity of about half the stack's random variance (3 standard errors). That is all the data support.
- **Settings it supports:** None. The filter width (3 px), the lag (6 px), the 10 px clearance and the tile size (64 px) are chosen in the code with their reasons and were not tuned.
- **Status:** Partly measured. One injection on one real pair of halves, and four real half-stack pairs of three fields (one night dithered every 10 exposures, one nearly undithered night too short in drift, two nights of a crowded field dithered at nearly every exposure; see [STK-44](#stk-44)). The reference-frame correction is a model (below). No field has both a dithered and an undithered night, so the effect of dithering is not measured.
- **Data you need:** A library stack of at least 16 light frames of one setting for the job; for the walking-noise rows, a drift of at least 12 px along one axis within a night. For the effect of dithering, a dithered and an undithered run of one field on one night, which the observatory does not hold (see [STK-44](#stk-44)).
- **Procedure:**
  1. Run the half-stack difference job ([STK-42](#stk-42)). It stacks the two halves and, from the same two files, stores eight more rows on the library stack: `stacking.half_shared_fine_scale_rms_stack_unit`, `stacking.half_shared_fine_scale_variance_ratio`, the three parts of the ratio (`stacking.half_shared_fine_scale_covariance_stack_unit_squared`, `stacking.half_fine_scale_difference_variance_stack_unit_squared` and `stacking.half_shared_fine_scale_reference_correction_stack_unit_squared`), `stacking.walking_noise_along_drift_correlation`, `stacking.walking_noise_across_drift_correlation` and `stacking.walking_noise_correlation_excess`.
  2. Read the rows and their errors.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  result = astro.processing.measure_half_difference_noise("M 57")
  print(
      result.shared_fine_scale_variance_ratio,
      result.walking_noise_correlation_excess,
      result.walking_noise_correlation_excess_error,
  )
  for name in (
      "stacking.half_shared_fine_scale_rms_stack_unit",
      "stacking.half_shared_fine_scale_variance_ratio",
      "stacking.half_shared_fine_scale_covariance_stack_unit_squared",
      "stacking.half_fine_scale_difference_variance_stack_unit_squared",
      "stacking.half_shared_fine_scale_reference_correction_stack_unit_squared",
      "stacking.walking_noise_along_drift_correlation",
      "stacking.walking_noise_across_drift_correlation",
      "stacking.walking_noise_correlation_excess",
  ):
      for row in astro.quality.query(metric=name, target_id="M 57", library_only=True):
          print(name, row.value, row.uncertainty, row.status.value, row.reason)
  ```

  `measure_half_pattern` <!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.measure_half_pattern --> does the measurement and `half_pattern_measurements` <!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.half_pattern_measurements --> builds the rows. The job reads each half stack once, as 32-bit (`read_stack_plane` <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_image.read_stack_plane -->), and gives the two planes to both the difference noise and the pattern. It takes the drift axis from the two half runs' registration records (`drift_of_runs` <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.drift_of_runs -->). A failure of the pattern step is logged and stored as a reason, and a failure while building its rows leaves the nine rows of the difference; neither loses the difference noise.

  The estimator. Both halves are high-pass filtered (the image minus its 3 px Gaussian smoothing; the empty edge is filled with the median first). The sample is the pixels at least 10 px from every faint source that the stack's faint-source mask flags ([STK-36](#stk-36)) and at least 9 px from the empty edge. The covariance of the two residuals over the sample is c. The registration reference frame is in both halves; its random noise is shared and is taken out as sigma_f^2 a b, where a and b are its weight shares and sigma_f^2 = D / F is one frame's variance: D is the variance of the difference of the residuals and F is the variance factor of [STK-42](#stk-42). The shared variance is c - sigma_f^2 a b. The random variance of the whole library stack at the same scale is D / (F N), with N the library stack's effective frame count. The ratio of the two is stored, and the root of the shared variance when it is above zero. All three parts (c, D and sigma_f^2 a b) are stored with their errors, so the ratio can be rebuilt from them, F, N, a and b (rows of [STK-42](#stk-42) and the stack's own effective frame count). The sums are taken in tiles of 64 by 64 px, and 400 bootstrap resamples of the tiles (a fixed seed) give the errors. The ratio's error leaves out the uncertainty of F and of N, and so does every row's.

  The reference-frame correction is a model. It assumes that every frame has the noise of the average frame (that is what F and sigma_f^2 = D / F mean), and the reference is the frame with the heaviest weight in Siril's star-width weighting, so equal noise is the least likely to hold for it. On M 57 it is large: 0.47 in ratio units (it is N a b, whatever F is), 20% of the uncorrected ratio of 2.32. It would have to be wrong by 70% to move the ratio by its error of 0.33, and with the correction doubled the ratio would still be 1.37.

  The walking-noise rows. The second half is moved by a lag of 6 px along the drift axis and by the same across it (bilinear interpolation, both signs averaged); the product with the first half, summed over the sample pairs whose four interpolation neighbors are all in the sample, divided by the geometric mean of the halves' variances, is the correlation. The drift axis is the one of [STK-44](#stk-44), found from the two half runs' transforms; it needs a drift of three errors of its slope, and the pointings must cover at least 12 px along it (twice the lag), or the rows are not measured with the reason. A pattern fixed on the sensor and smeared by the drift is correlated along it to a lag of about its length, and the signal at the lag falls as about (1 - lag / extent) of the pointing range along the axis (0.53 for M 57's 12.7 px). Faint sky structure is not smeared and is correlated alike in both directions. Stars that are elongated along the drift (a trail from field rotation or a guiding drift) are also correlated more along it than across it, and add to the excess.
- **Acceptance criterion:** Not recorded. The injection below gives the strength at which the excess is seen, which is the only criterion the data support.
- **What to look at:** The excess with its error. An excess above 3 errors on a drifting run says a smeared pattern is present, or stars are elongated along the drift (look at the stack); the variance ratio next to it says how large. A ratio above zero with an excess near zero says the halves share structure that does not move with the drift (sky, or a pattern that does not move with the pointing). The correlations on their own are not a result; both are small and negative at 6 px because of the filter's side lobes. A ratio is read together with its correction: a ratio below its correction (here 0.47) is not distinguished from the reference frame's noise being mis-modeled.
- **Our result:** 2026-10-23 and 24, on scratch copies. The production job on the M 57 library stack (night of 2026-10-03, 61 frames of 60 s, dithered every 10 exposures by 2.7 to 5.8 px, ZWO ASI 533MM Pro, the private copy of [STK-42](#stk-42), 58 s for the whole job, peak memory of the process 800 MB) stored 17 rows; the eight of this entry are:

  | Quantity | M 57 value |
  | --- | --- |
  | Pixels in the sample | 3.20 million of 9.05 million (2,209 tiles) |
  | Covariance c of the halves | 7.38e-9 (+- 1.05e-9) stack units squared |
  | Variance of the difference D | 1.098e-8 (+- 1.3e-11); variance factor F 0.0845, library effective frame count N 40.78 |
  | Reference-frame correction D / F a b | 1.512e-9 (+- 1.8e-12), with a = 0.1075 and b = 0.1083 |
  | Shared variance of the halves c - D / F a b | 5.87e-9 (+- 1.05e-9) |
  | Random variance of the whole stack, D / (F N) | 3.19e-9 |
  | Ratio of the two | 1.84 (+- 0.33) |
  | Root of the shared variance | 7.7e-5 (+- 0.7e-5), 1.2% of the sky level 6.3e-3 |
  | Correlation along the drift (axis 37.6 degrees, pointings range over 12.7 px) | -0.0165 (+- 0.0014) |
  | Correlation across the drift | -0.0160 (+- 0.0018) |
  | Excess | -0.0006 (+- 0.0015) |

  The shared variance, the ratio and the correlations equal those of the earlier run of the same job (before the three parts were stored and the halves were read as 32-bit) to the digits shown, and the difference noise (1.0535756660888078e-3 stack units) is the same to the last digit: two 32-bit numbers this close subtract exactly. So the halves share a variance 1.8 times the library stack's own random variance at the scale of a few pixels, and none of it moves with the drift to within the error. The error of c (1.05e-9) is mostly the scatter of the sky structure over the tiles.

  Injection on the same two real halves. A white pattern (one value per detector pixel) was moved by each frame's real pointing shift (the 10-frame dithers and the drift of this night) and averaged over the frames of each half, then added to the half, with its size set as a multiple s of the library stack's random rms at this scale (5.6e-5). The recovered ratio is the real ratio plus the injected variance (s squared), and the excess rises with it:

  | Injected s | Injected variance ratio | Measured ratio | Excess (+- error) |
  | --- | --- | --- | --- |
  | 0 | 0 | 1.84 (+- 0.33) | -0.0006 (+- 0.0015) |
  | 0.1 | 0.01 | 1.85 | -0.0004 (+- 0.0015) |
  | 0.2 | 0.04 | 1.87 | -0.0001 (+- 0.0015) |
  | 0.5 | 0.25 | 2.06 | +0.0019 (+- 0.0015) |
  | 1.0 | 1.0 | 2.71 | +0.0078 (+- 0.0014) |
  | 2.0 | 4.0 | 5.12 | +0.0203 (+- 0.0013) |

  The measured ratio gains 0.22, 0.87 and 3.28 for injected variances of 0.25, 1 and 4 (12%, 13% and 18% low, the last inside the error of 0.3). The excess rises by 0.0083 to 0.0100 for each unit of injected variance ratio, and is seen at 5 errors at s = 1 and not at s = 0.5. So the walking test sees a white, pixel-scale pattern from an injected variance ratio of about 0.5 (3 errors, an excess of 0.0046). It calibrates the geometry of this one night (a 12.7 px range of dithers and drift, and a lag of 6 px); a different range or lag gives a different slope.

  What the null result means. The grain that the master flat is expected to leave is far below this sensitivity: the flat's noise is 0.119% of its level ([STK-39](#stk-39)), which is 7.5e-6 of M 57's sky level 6.28e-3, a variance of 5.6e-11, a ratio of 0.018 to the stack's random variance of 3.19e-9. That is about 25 times below the 0.5 that the walking test sees (the review board's rough estimate was 10 times). The null excess was therefore expected, and it does not show that the flat leaves no pattern. The supported statement is that the M 57 halves hold no white, pixel-scale pattern moving with the pointing at a variance of about half the stack's random variance or more. It is not "80% of the shared variance is sky": an earlier reading of this entry gave that figure, from an excess at 2 errors above its value read as a pattern of 0.3 to 0.4 of the random variance, and the injection does not support so exact a split. Not ruled out: a pattern wider than the 6 px lag (flat errors correlated over 10 px or more are correlated alike along and across at this lag), a pattern that does not move with the pointing (optics, a fixed ghost), and the reference-frame correction being wrong. The ratio of 1.84 therefore stays a measure of what the halves share, with sky structure as the likely main part (the other pairs below range from 0.2 to 4.3 with the field) and not an inference about the sensor.

  Other real pairs (stacked by `science/s4/pattern_run.py`: the whole night's frames first, to find Siril's own reference and the whole stack's effective frame count, then the two halves with that frame in both; the stacks were not written to the copy's stacks folder; the numbers were made before the three parts were stored and the halves were read as 32-bit, and the code is otherwise the same):

  | Night | Frames in halves | Median step (STK-44) | Drift axis range (px) | Shared variance / random variance | Excess (+- error) |
  | --- | --- | --- | --- | --- | --- |
  | M 81 2026-01-16, 60 s | 15, 16 | 0.20 px | 2.8 | 0.21 (+- 0.006) | not measured: the drift is shorter than 12 px |
  | M 27 2026-09-24, 60 s | 17, 17 | 2.43 px | 83.7 | 1.14 (+- 0.026) | -0.0002 (+- 0.0011) |
  | M 27 2026-09-23, 30 s | 24, 25 | 2.23 px | 93.5 | 4.30 (+- 0.22) | -0.0018 (+- 0.0013) |

  The shared variance of the halves ranges from 0.21 to 4.3 times the random variance with the field (a sparse field, a crowded Milky Way field) and not with the dithering: the two dithered M 27 nights differ by a factor of 4 from each other, and the dithered M 27 night with 33 frames has less than the M 57 night dithered every 10 exposures. The excess of both M 27 nights is zero within 1.4 errors. M 27 2026-10-02 (18 frames of 120 s) stacked as a whole night but Siril left no single reference frame, so no halves; NGC 2244 2026-01-17 has 11 frames of one exposure, too few for halves of 8. **There is no pair of a dithered and an undithered run of one field on one night, so this entry does not measure the effect of dithering.** The alternating split gives each pointing held for several exposures to both halves, so a dither every 10 exposures (M 57) leaves both halves the same set of pointings and does not reduce what they share; only a dither with a new pointing at every exposure (M 27) could.

  Memory and time of the pattern step. On synthetic stacks (shared sky structure and about 750 to 6,000 stars, independent noise; `science/s4/memory_time.py`, each size in its own process through the real-run script, with the library's imports counted: the process starts at 184 MB):

  | Stack | Planes | Peak after reading | Peak after the measurement | Read | Measurement |
  | --- | --- | --- | --- | --- | --- |
  | 3008 x 3008, one channel | 32-bit, 36 MB each | 288 MB | 608 MB | 0.0 s | 5.4 s |
  | 6000 x 4000, three channels (averaged) | 32-bit, 96 MB each | 917 MB | 1,307 MB | 0.4 s | 13.9 s |
  | 3008 x 3008, one channel | 64-bit, 72 MB each | 357 MB | 746 MB | 0.0 s | 5.4 s |
  | 6000 x 4000, three channels | 64-bit, 192 MB each | 1,374 MB | 1,673 MB | 0.6 s | 14.0 s |

  The measurement adds about ten copies of one plane's 32-bit size above the two planes (the faint-source mask and its distance map, the two filtered residuals, the lagged copy and the products), for example 608 MB = 184 MB for the process, 72 MB for the planes and about 350 MB of working arrays. The job on the M 57 library stack ran in 58 s with a peak of 800 MB for the process (without Siril, which runs apart). The half stacks of the job are read once as 32-bit, as the job passes them on; the figures are for the pattern step and the reads only, and an earlier version (64-bit planes read twice, an array of ones for each count) was not measured.

  To repeat: `science/s4/injection.py`, `science/s4/pattern_run.py` (arguments: target, night), `science/s4/real_m57_job_v2.py` and `science/s4/memory_time.py` (arguments: width, height, channels, precision) in the session's scratch folder, or the call in the procedure.
- **Verdict:** Not judged.
- **Limits:** The shared variance is the part of any fixed pattern that stays coherent in both halves plus faint sky, and it is dominated by sky structure on every field tried; it is not an upper limit of the sensor pattern. The excess is the only part that points to the sensor, and it needs a drift of 12 px or more along one axis within a night. Its sensitivity (a variance ratio of about 0.5 for a white pattern, on M 57's geometry) is 25 times above the grain a flat is expected to leave, so a null excess is expected and says little about the flat. Stars elongated along the drift add a positive excess, so a positive excess is not alone a sensor pattern. A pattern that does not move with the pointing (a fixed ghost, a pattern in the optics) is not smeared and would be counted with the sky. A pattern correlated over more than the 6 px lag is not told from sky. The equal-frame-noise model for the shared reference frame's noise is the one of [STK-42](#stk-42); the correction is not small (0.47 in ratio units, 20% of the uncorrected ratio on M 57), and the reference has the heaviest weight, so the model is weakest for it. The errors treat the 64 px tiles as independent; a smear 84 px long is longer than a tile, so the errors of a long drift are somewhat too small, and they leave out the uncertainty of F and N. Interpolating the second half at a non-integer lag smooths its residuals a little, the same for the two directions only on average. Nothing was tuned: the filter width, the lag and the clearance were chosen once, with reasons in the code. Spectral stacks are not measured (the job refuses them).
- **To tune for your setup:** None.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_half_pattern.py` checks that noise alone shares nothing, that a shared pixel pattern is recovered in size, that a smeared pattern is more correlated along the drift than across for three drift directions (0, 90 and 37 degrees), that the wrong axis gives a negative excess, that smooth sky structure leaves the excess at zero, the removal of the shared frame's noise, that the stored parts rebuild the ratio, that pixels are counted from the mask, every not-measured case and the eight rows. `astrometricslib/pipelines/stacking/test/test_half_difference_noise.py` checks that the job stores them, that each half is read once as 32-bit and shared, and that a failure while building the pattern rows leaves the nine difference rows.
- **Raw record:** None kept in the repository. The scripts and their output are in the session's scratch folder (`science/s4/`); the rows were stored in the quality store of the copy.

### STK-46

**Flat-to-light match: do the flats still show the lights' dust**

- **Claim:** The dust shadows of the mean of the raw flats can be compared with the dust in the raw lights, which the stacking step does not keep. The question is whether the flats' dust shadows still match the lights (dust moved, added or cleaned since the flats), not whether the camera turned: shadows about 300 pixels across at f/5.4 come from dust about 6 mm from the sensor, on the camera window, which turns with the sensor, so the metric cannot see the camera's rotation relative to the optics. On synthetic frames with shadows at the real depth (0.15 to 0.4%) and real noise, the search recovers a shift with a root-mean-square error of 1.1 pixel and a rotation of the shadow pattern with 0.19 degree, in all 64 trials. The placement is given only when the best score stands 3.1 null spreads above the same search at displaced placements, the 99th percentile of that statistic on 101 pairs with unrelated dust (false-alarm rate 1 in 101). Whether a low correlation means the flats should be retaken is not known.
- **Settings it supports:** None. The map scales and limits (cell of 8 pixels, band pass of 2 and 30 cells, 16 flats, 24 lights, a target disc of 25 cells, a sky of 10 times the read noise, shadow limits of 5 noise deviations, 0.15% and 25 cells, a placement limit of 3.1 null spreads, a method error of 1.1 pixel and 0.2 degree) are exploratory choices made on the M 57 night and are listed here only as what the numbers below used.
- **Status:** Partly measured. The method is checked on synthetic frames with a known answer at real depth and run on 15 real groups of one telescope with two cameras. No real group has a known displacement, so the real shifts and rotations are not checked.
- **Data you need:** A stack with its flat and bias frames in the calibration library, at least 8 raw light frames of the stack's size on disk, and a camera profile with the gain and read noise at the frames' gain, offset and binning (the bias level of the lights comes from the bias frames, because the profile holds none). Better, flats and lights taken on nights with a known change of the dust between them (a cleaning of the sensor window).
- **Procedure:**
  1. Stack the target. The staging step records the library files of the flats and biases it linked (`flat_frame_paths` and `bias_frame_paths` in the run's diagnostics). The record step stores ten rows on the stack, in a call of their own after the stack's rows and the frame geometry rows: `stacking.flat_dust_shadow_count` (a diagnostic), `stacking.flat_light_dust_correlation_at_zero_offset`, `stacking.flat_light_correlation_outside_shadows`, `stacking.flat_light_best_correlation`, `stacking.flat_light_best_shift_x_px`, `stacking.flat_light_best_shift_y_px`, `stacking.flat_light_best_rotation_deg`, `stacking.flat_light_displaced_correlation_std`, `stacking.flat_light_search_excess_sigmas` and `stacking.light_dust_split_half_correlation`. It samples only the frames that went into the stack. The progress report says the step reads up to 16 flat, 24 light and 5 bias frames again, and the log says how many.
  2. Read the rows and compare the best correlation with the search excess, the control correlation and the split-half value.
  3. To measure without a stack, give the flat, light and bias file paths and a `SkyRequirement` to `measure_flat_match`.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for name in ("stacking.flat_light_best_correlation", "stacking.flat_light_search_excess_sigmas"):
      for row in astro.quality.query(metric=name, target_id="M 57"):
          print(name, row.value, row.status.value)
  ```

  `measure_flat_match` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.measure_flat_match --> builds both maps from the raw frames, `band_pass_dust_map` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.band_pass_dust_map --> keeps the scale of a dust shadow, `count_dust_shadows` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.count_dust_shadows --> counts the shadows in the flat map, `correlation_outside_shadows` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.correlation_outside_shadows --> is the control, and `match_dust_maps` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.match_dust_maps --> searches the shift and rotation and the displaced null. The staging step lists the flat and bias frames it used in its diagnostics, and `flat_match_rows` <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.flat_match_rows --> makes the rows. A time limit from the configuration (`flat_match_time_limit_seconds`, default 300) is checked before each frame is read; when it is reached the rows are not measured and the reason says how many frames were read.
- **Acceptance criterion:** Not recorded. A limit on the correlation that says "take or retake the flats" needs lights and flats from nights with a known change of the dust, and the observatory has none.
- **What to look at:** The best correlation against the search excess (a placement is given only above 3.1 null spreads); the control correlation with the shadows masked out, which shows whether the correlation comes from the shadows' cores; the split-half correlation, which is the highest repeatability the lights' map has; the shift and rotation together with their uncertainty; and the shadow count, which is a diagnostic and gates nothing.
- **Our result:** 2026-10-24, frames read from the library copy (local disk). The maps are built from raw frames, 8 x 8 pixel cells, with the bias level taken off the lights, a disc of 25 cells around the frame center set aside, a polynomial of degree 4 for the vignetting divided out and a band pass of 2 and 30 cells (16 and 240 pixels). On the M 57 flat the shadows are discs about 300 pixels across and 0.15 to 0.4% deep in the band-passed map; the flat map's noise is 0.003% and the light map's noise 0.03%.

  Injection at the real depth (a synthetic sensor of 2400 x 2400 pixels, soft discs of radius 100 to 170 pixels and depth 0.15 to 0.4%, a sky of 600 ADU above a bias of 100 ADU with 25 ADU of pixel noise, flats with 0.2% pixel noise, eight stars that jitter by 3 pixels between frames, 24 lights; eight random dust sets and eight displacements each, shifts to 130 pixels, rotations of the shadow pattern from -2 to +2.5 degrees). All 64 trials gave a placement (the smallest search excess was 6.1). The error of the shift along x had a median of 0.73 pixel, a 90th percentile of 1.65, a largest of 2.66 and a root-mean-square value of 1.06; along y, 0.50, 1.73, 2.94 and 0.98; the rotation, 0.18, 0.25, 0.39 and 0.19 degree. These are the errors the placement rows carry as the method error (1.1 pixel, 0.2 degree). The odd/even half-difference is one split pair that sees noise only (up to about 1.3 pixel); the stored uncertainty is the square root of the sum of its square and the square of the method error, and it is a bound, not a statistical 1σ. The earlier injection used shadows 10 times too deep (1.2 to 2.5%): it gave a median shift error of 0.8 pixel, but its uncertainty (0.01 to 0.34 pixel) was far below the error.

  The null of the placement. The search takes the best of 25 angles and 41 x 41 shifts, so a high score happens by chance. The same search is run with the lights' map moved by 60 cells in 8 directions, and the real best score is read as `stacking.flat_light_search_excess_sigmas`: its excess over the mean of those 8 best scores in units of their sample standard deviation. Unrelated dust gave this statistic on 101 pairs: 56 synthetic pairs (the lights of one dust set against the flats of each of the other seven, at the real depth: mean -0.01, standard deviation 1.13, 95th percentile 1.8, 99th percentile 3.2, largest 3.29) and 45 real ones (each real group's lights against its flat map flipped up-down, left-right and turned a half turn: same shadows in other places; largest 2.76). The pooled 99th percentile is 3.05 (the second-largest value; the largest is 3.29), rounded up to 3.1, which is the placement limit (it replaces a limit of 5 chance spreads of another statistic, with no measurement behind it). At 3.1, 1 of the 101 unrelated pairs is above the limit, a measured false-alarm rate of 1.0% (a 95% interval from 0.03 to 5%). The 64 true pairs were all above 6.1. Scripts: `science/s6/injection2.py` (output `injection2.out`) and `science/s6/real_flat_match2.py` (output `real_flat_match3.out`) in the session's scratch folder.

  Real data (the ASI 533MM Pro on the Apertura 75Q, flats of 2026-10-03, and the Nikon D5300 on the Apertura, flats of 2024-02-01). "r0" is the correlation with no shift or rotation, "outside" the same with the flat's shadows masked, "best" the correlation at the best placement, "excess" the search excess in null spreads, "split" the split-half correlation. A dash means the placement rows were not measured. The profile of the ASI 533MM holds a gain and read noise only for gain 0, offset 10, so the sky requirement was applied to the first two rows; for all the others the run used no requirement (the library would store these as not measured: see Limits). All 24 sampled lights passed in every group. The bias level came from the library's bias frames.

  | Lights | Flats | r0 | outside | best | excess | split | Placement (x px, y px, deg) |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | M 57 2026-10-03 (ASI) | same afternoon | 0.321 | 0.306 | 0.355 | 6.9 | 0.81 | -1.7, 2.3, -0.17 |
  | NGC 7331 2026-10-03 (ASI) | same afternoon | 0.606 | 0.528 | 0.627 | 9.5 | 0.37 | 0.6, -0.7, -0.10 |
  | M 81 2026-01-16 (ASI) | 8.5 months later | 0.434 | 0.412 | 0.479 | 7.0 | 0.86 | 0.1, 1.3, -0.30 |
  | NGC 2403 2026-02-22 (ASI) | 7.5 months later | 0.421 | 0.407 | 0.461 | 10.2 | 0.68 | 0.9, -0.2, -0.25 |
  | Mirach 2026-09-24 (ASI) | 9 days earlier | 0.538 | 0.470 | 0.557 | 6.5 | 0.48 | 1.7, 0.6, -0.17 |
  | Navi 2026-09-24 (ASI) | 9 days earlier | 0.124 | 0.097 | 0.172 | 2.6 | 0.22 | - |
  | M 81 2024-01-31 (Nikon) | next day | 0.832 | 0.806 | 0.839 | 2.7 | 0.96 | - |
  | NGC 2024 2024-01-31 (Nikon) | next day | 0.521 | 0.488 | 0.545 | 2.9 | 0.95 | - |
  | NGC 4438 2024-03-17 (Nikon) | 6 weeks later | 0.058 | 0.065 | 0.064 | 0.7 | 0.80 | - |
  | NGC 6888 2024-08-04 (Nikon) | 6 months later | 0.030 | 0.035 | 0.067 | 0.5 | 0.85 | - |
  | NGC 6992 2024-09-06 (Nikon) | 7 months later | 0.045 | 0.020 | 0.068 | 0.8 | 0.86 | - |
  | M 81 2023-05-13 (Nikon) | 9 months earlier | -0.040 | -0.008 | 0.041 | -0.4 | 0.66 | - |
  | NGC 7000 2023-12-13 (Nikkor) | 12 months later | 0.212 | 0.207 | 0.230 | 1.1 | 0.96 | - |
  | NGC 1499 2023-12-15 (Nikkor) | 12 months later | 0.367 | 0.359 | 0.367 | 2.4 | 0.41 | - |
  | NGC 7023 2023-10-08 (Nikkor) | 11 months later | 0.405 | 0.396 | 0.415 | 3.2 | 0.65 | -2.0, -6.4, 0.13 |

  The flat shadow count was 9 for every ASI group (14 for the Nikon, 6 for the Nikkor). Where a placement was given, its uncertainty (the half-difference of the odd and even placements combined with the method error) was 1.1 to 2.6 pixels along each axis and 0.20 to 0.33 degree. The five ASI groups with a placement lie within 2.3 pixels and 0.30 degree of zero, which is within about twice the method error (1.1 pixel, 0.2 degree), so they show no movement of the shadow pattern between the flats of 2026-10-03 and the nights from 2026-01 to 2026-10. Navi (2026-09-24) is the group to look at: its correlation with the flats is low (0.12, excess 2.6) and its light map is less repeatable (split-half 0.22). Before the changes of this review (no bias subtraction, no target disc, a limit of 5 chance spreads of the zero-rotation search) it read r0 0.14, best 0.19 and split-half 0.46; the bias level, the disc around the star at the field's center and the new null changed its light map, and the cause of the low value is not established (the star is bright and sits where the disc now sets cells aside). Mirach, which had no placement (4.5 chance spreads of a limit of 5), now has one (6.5 null spreads, 1.7 and 0.6 pixel) because the bias subtraction deepens its shadows (r0 0.36 before, 0.54 now). The M 81 and NGC 2403 correlations rose by 0.04 to 0.07 for the same reason.

  The Nikon: a high correlation is not a dust match. M 81 and NGC 2024 of 2024-01-31 (the day after the flats) have r0 0.83 and 0.52, but their control with the shadows masked is 0.81 and 0.49, the flat map flipped up-down scores 0.81 and 0.55 in the search with the same lights, and their search excess (2.7 and 2.9) is under the limit. The correlation is carried by large structure shared by the flat and the lights (probably a vignetting leftover that is nearly symmetric), not by the shadows' cores; the cause was not examined further. This is the case the control was added for. The Nikon lights of six weeks later or after give -0.04 to 0.06 with no placement and the Nikkor lights 0.2 to 0.4. NGC 7023 (Nikkor, 3.2 null spreads, 11 months after the flats) is the one real group not on the ASI above the limit, and it gives a placement of -2.0 and -6.4 pixels. With 15 groups the chance of at least one false alarm at a rate of 1% is 14%, so this is not evidence of a match.

  Cost: on the local disk, reading 16 flats, 24 lights and 5 bias frames and measuring took 10 to 18 seconds for the ASI frames of 3008 x 3008 pixels and 30 to 34 seconds for 6000 x 4000 Nikon frames (64 seconds with a cold disk cache), which includes the 8 displaced searches and the odd/even searches. The process peaked at 520 MB (ASI) and 980 MB (Nikon). These frames are in the library copy on the local disk, not on the rclone mount (the mount at `/home/michael/drive` holds only a folder of deleted library files), so the time on the real storage tier is not measured. The reading is the part that depends on storage, and the time limit (300 s by default) bounds it.

  To repeat: `science/s6/real_flat_match2.py` and `science/s6/injection2.py` in the session's scratch folder, or the call in the procedure.
- **Verdict:** Not judged. Insufficient evidence: no group has a known displacement, so the real shifts and rotations are not checked, and no group has a known case of flats that should be retaken. The review date of the exploratory metrics is 2027-04-30.
- **Limits:** The shadow count depends on its limits (0.15%, 25 cells, 5 noise deviations): at 0.1% and 9 cells the M 57 flat gives 71. It gates nothing. The camera does not enter: the shadows sit on the camera window, so the shift and rotation are those of the shadow pattern on the sensor and say nothing about the camera's rotation relative to the optics. The placement limit comes from 101 unrelated pairs, so its own uncertainty is large (one pair more or less above it moves the false-alarm rate by 1 point); the 8 displaced searches give a sample standard deviation from 8 values. The control correlation leaves a faint halo around each shadow that the mask does not cover, so with deep dust it stays positive: read it against the value with the shadows. The bias level of the lights is subtracted and the flats' is not. A bias offset makes a shadow shallower by the fraction bias / (bias + signal): 0.4% of its depth in a flat of 28,000 ADU with a bias of 100 ADU (negligible), but 14% in lights of 600 ADU of sky with the same bias, which is probably why the earlier numbers without the subtraction were lower (not checked by a separate run). The bias level comes from the stack's bias frames; with none the rows are not measured (the camera profile holds no bias level). The sky requirement (10 times the read noise above the bias) needs the profile's gain and read noise at the frames' gain, offset and binning; the ASI 533MM's profile has them only for gain 0, offset 10, so for the other settings, and for the Nikon, which has none, the rows are stored as not measured. A disc of 25 cells (200 pixels, 6.4 arcminutes) around the frame center is set aside as the target; a larger target, or one off the center, can still reach the light map. Cell-level masks (this disc, the star masks) cannot reuse the stacking step's empty-aperture mask, which is made on the stack in stack pixels. The flats are read twice per stack (once for the flat assessment, once here). The chance spread of the zero-offset correlation is large for a few broad shadows (up to 0.25 on the Nikon). The rotation is least certain for shadows near the sensor center. The light map holds whatever repeats from frame to frame besides dust (the sensor's fixed pattern, stars too faint to find), so the correlation is below the split-half value even for a perfect flat. A spectral stack is not measured. Lights of a different size than the flat's, fewer than 8 usable lights, and a time limit reached give not-measured rows with the reason. Only the first usable frame's size is used. The placement search covers 160 pixels and 3 degrees (no half turn); a larger displacement puts the peak at the edge and the rows are not measured.
- **To tune for your setup:** None. If your dust shadows are much smaller or larger than 300 pixels, change the cell size and the band pass widths in `pipelines/stacking/post_processing/flat_match.py` and repeat the injection.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/stacking/test/post_processing/test_flat_match.py` checks the recovery of a known shift and rotation (both signs, a color mosaic), the unrelated-dust, half-turn and no-dust cases, the control correlation, the uncertainty in quadrature, the bias subtraction, the sky requirement and the frames it leaves out, the target disc, the time limit with a fake clock, the member list and its order, every not-measured case, the band pass against a vignetting that is not a polynomial, and that the record step stores the rows in their own call and stores no match row when the step fails. `astrometricslib/drivers/test/test_calibration_matching.py` checks that the staging step records the flat and bias files it linked.
- **Raw record:** None kept.

## Design estimates

These tuned values have not been measured. Each is a reasoned choice; the last column says how you could measure it on your data.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| STK-D01 | Fewest frames per master calibration frame | 3 | config key `[Processing.Siril] minimum_calibration_frames` | Noisy master calibration frames add noise to every stack. | Build masters from 1, 3, 5 and 10 frames and compare stack noise with `QualityRecords.measure_stack(path, compare_to=...)`; the minimum is where the gain stops mattering. |
| STK-D02 | Rejection limits for bias and dark masters | `rej 3 3` | Siril script in `drivers/siril_interface.py` | Masters with more noise or leftover hot pixels. | Stack masters at 2.5 and 3.5 and compare their noise and hot-pixel counts. |
| STK-D03 | Fewest frames in an exposure group | 5 | code constant `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` in `pipelines/stacking/processing/exposure_groups.py` | Groups that register poorly or reject too many pixels. | Stack groups of 3, 5 and 8 frames with `ProcessingPipelines.stack(frames=...)` and check the registration share and rejected fraction. |
| STK-D04 | Saturated pixel share that flags a stack | 0.1% | code constant `DEFAULT_SATURATION_FLAG_THRESHOLD` in `pipelines/shared/quality/saturation.py` | Normal stacks flagged as saturated, or saturated stacks missed. | Run `report_gate_limits` across your stacks and see where normal stacks sit. |
| STK-D05 | Flat level limits | 10% and 90% of full scale | code constants `MINIMUM_FLAT_LEVEL_FRACTION`, `MAXIMUM_FLAT_LEVEL_FRACTION` in `pipelines/stacking/pre_processing/flat_calibration.py` | Flats that are too dim or too bright give uneven flatness. | Take flats at 5% to 95% of full scale, stack lights with each, and compare flatness with `QualityRecords.measure_stack(..., compare_to=...)`. |
| STK-D06 | Flat blur width limits | 1 to 8 pixels | code constants `MINIMUM_SMOOTHING_SIGMA_PIXELS`, `MAXIMUM_SMOOTHING_SIGMA_PIXELS` in the same file | Dust shadows blurred away, or noise left in the flat. | Blur a noisy master by several widths and check that dust shadows survive in the stack's flatness measurement. |
| STK-D07 | Dark exposure match | within 0.1 s | `CalibrationLibrary.select_dark_frames` in `drivers/calibration_library.py` | Mismatched darks over- or under-subtract. | Compare masters of nearby exposures with `CalibrationCatalog.query` and the stack's negative-pixel warning. |
| STK-D08 | Noise penalty that switches on weighting | 1.05 | code constant `MINIMUM_PENALTY_TO_WEIGHT` (STK-07) | Weighting switched on when it does not help, or off when it does. | Stack mixed sessions with P between 1.0 and 1.2 both ways and compare the noise. |
| STK-D09 | Spectral registration share before a retry | 0.9 | code constant `MINIMUM_REGISTERED_FRACTION` (STK-12) | Spectral groups retried too often or too late. | Read the per-group frames stacked and submitted on your spectral sessions. |
| STK-D10 | Brightness-scale pixel method | 20th to 80th percentile; brightest 1% | code constants `GAIN_MID_RANGE_PERCENTILES`, `GAIN_BRIGHTEST_FRACTION` in `pipelines/stacking/processing/exposure_groups.py` | A wrong brightness scale between exposure groups. | Compare `measure_group_gains` with `measure_group_gains_with_stars` on imaging groups (STK-19). |
| STK-D11 | Saturation level of a group stack | 0.95 of full scale | code constant `SATURATION_FRACTION_OF_FULL_SCALE` in the same file | Saturated pixels treated as good data, or good pixels masked. | Read the plateau of a saturated star in your group stacks with `estimate_saturation_mask_level`. |
| STK-D12 | Clipping floor for clipped groups | 2 frame-noise widths | code constant `CLIPPING_FLOOR_SIGMAS` in the same file | Clipped groups mis-weighted at faint pixels. | Compare a clipped group with an unclipped one of the same field at faint pixels. |
| STK-D13 | Session gap for the quarantine | 4 hours | code constant `SESSION_GAP_SECONDS` in `pipelines/stacking/pre_processing/frame_quarantine.py` | Frames from one session judged against another session's median. | List the gaps between your frames with `QualityRecords.measure_frames(kind="raw_check")`. |
| STK-D14 | Star-pattern matching constants | 150 stars, 8 neighbors, 1% shape tolerance | code constants in `pipelines/stacking/processing/star_pattern_candidates.py` | Star-pattern matches missed or false between group stacks. | Run `find_alignment_from_star_patterns` on group pairs of your camera and count matches found. |
| STK-D15 | Pre-fetch readers | 4 | config key `[Processing] prefetch_workers` | Slow reads on a network drive. | Time a stack with 1, 2, 4 and 8 readers on your network drive; the job log's `Pre-fetch:` line gives the time. |
| STK-D16 | Siril runs at once | 2 | config key `[Processing.Parallelism] max_concurrent_jobs` | Slower stacking or failed Siril runs. | Run `python -m astrometricslib.scripts.benchmark_siril_concurrency --target A --target B --slots 1 --slots 2` and pick the fastest count with no failures. |
| STK-D17 | Color calibration star finder | 8σ; 3.5 px circle; 7 to 11 px ring | code constants `DETECTION_SIGMA`, `APERTURE_RADIUS_PIXELS`, `ANNULUS_INNER_RADIUS_PIXELS`, `ANNULUS_OUTER_RADIUS_PIXELS` in `pipelines/stacking/post_processing/colour_calibration.py` | Color gains that change by more than their uncertainty. | Run `measure_gains` with each value moved and see whether the gains change by more than their uncertainty. |
| STK-D18 | Flat saturation level | 98% of full scale | code constant `_SATURATED_FRACTION_OF_FULL_SCALE` in `pipelines/stacking/pre_processing/flat_calibration.py` | Saturated flat pixels counted as good, or good ones counted as saturated. | Take flats at rising exposure and note the level where the share of clipped pixels starts to rise. |
| STK-D19 | Raw-frame check: high star count | 1.4 times the batch median | code constant `HIGH_STAR_MULTIPLE` in `pipelines/shared/quality/raw_frame_check.py` | Good frames flagged for a high count, or frames with trails and light leaks missed. | Run the raw check on many clean batches (`QualityRecords.measure_frames(kind="raw_check")`) and set the limit above the largest normal ratio. |
| STK-D20 | Raw-frame check: faint-trail detector | 4 px blocks; 3σ block threshold; 60σ star mask; trail at least 0.25 of the frame diagonal | code constants `TRAIL_BLOCK_PIXELS`, `TRAIL_THRESHOLD_SIGMA`, `TRAIL_STAR_SIGMA`, `TRAIL_MIN_DIAGONAL_FRACTION` in `pipelines/shared/quality/raw_frame_check.py` | Faint trails missed, or star chains and noise flagged as trails. | Add synthetic trails of known brightness to clean frames and count how many `line_trail_px` finds; run it on clean frames to count false trails. |
| STK-D21 | Frame-trend limits | at least 4 frames; at least 70% of steps in one direction | code constants `MINIMUM_FRAMES`, `STEADY_STEP_FRACTION` in `pipelines/shared/quality/frame_trends.py` | Slow drifts missed, or noise reported as a trend. | Run the trend check on sessions with a known drift (dew, focus) and on steady ones, and count false and missed trends. |
| STK-D22 | Frame measurement job: frames measured at once (worker processes); bias frames read per setting; frames measured in one stack | 4; 5; 40 | code constants `MEASUREMENT_WORKER_COUNT`, `BIAS_FRAMES_FOR_LEVEL`, `STACKING_STAGE_MEASUREMENT_LIMIT` in `pipelines/stacking/frame_measurement_run.py` <!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.MEASUREMENT_WORKER_COUNT --> <!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.BIAS_FRAMES_FOR_LEVEL --> <!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.STACKING_STAGE_MEASUREMENT_LIMIT --> | A slow job that makes the app sluggish, a bias level thrown off by one odd frame, or a first stack that waits too long. | Measured 2026-10-22 on the two golden M 13 frames (3008 by 3008 px, 8 frames per row, 12 cores): seconds per frame with 1, 2 and 4 worker threads 14.1, 10.6 and 11.2; with 2 and 4 worker processes 8.8 and 5.9. Threads stop gaining at two because the fitting holds Python's global lock, so the job uses processes. Re-time `ProcessingPipelines.record_frame_measurements` on 40 frames with 1, 4 and 8 workers while the app is open; compare the bias level of one setting from 1, 5 and 10 bias frames. |
| STK-D23 | Stack noise measurement: apertures; aperture radius; reference stars; sky ring; clearance from stars; shift search radius; faint-source threshold; smoothing widths; background subtraction width; clearance from faint sources; depth factor for replacing a set | 100; 4 px; 10; 7 to 12 px; 24 px; 500 px; 3.5σ; 1.5, 3 and 6 px; 12 px; 13 px; 2 | code constants `NOISE_APERTURE_COUNT`, `APERTURE_RADIUS_PX`, `REFERENCE_STAR_COUNT`, `RING_INNER_PX`, `RING_OUTER_PX`, `STAR_CLEARANCE_PX`, `SEED_SEARCH_RADIUS_PX`, `FAINT_SOURCE_THRESHOLD_SIGMAS`, `FAINT_SOURCE_SMOOTHING_SIGMAS_PX`, `FAINT_SOURCE_HIGH_PASS_SIGMA_PX`, `FAINT_SOURCE_CLEARANCE_PX`, `REPLACEMENT_DEPTH_FACTOR` in `pipelines/stacking/post_processing/empty_apertures.py` (`APERTURE_RADIUS_PX`, `RING_INNER_PX` and `RING_OUTER_PX` are in `stack_image.py` and `SEED_SEARCH_RADIUS_PX` is in `stack_noise.py`, same folder) (STK-36) <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.STAR_CLEARANCE_PX --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_HIGH_PASS_SIGMA_PX --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.REPLACEMENT_DEPTH_FACTOR --> | A noisy or biased stack noise value, or apertures placed on the wrong stars. | Repeat the STK-36 run on another target and camera with 50, 100 and 200 apertures, and check the shift against the true star offsets. |
| STK-D24 | Subset stacks: fewest frames in a subset; growth steps | 4; 4 | code constants `MINIMUM_SUBSET_FRAMES`, `DEFAULT_GROWTH_STEPS` (STK-37) | Subsets too small to reject outliers, or a growth curve with too few points. | Stack subsets of 3, 4 and 6 frames and compare how many pixels each rejects. |
| STK-D25 | Half-difference noise: fewest frames in each half, counting the shared reference frame | 8 | code constant `HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF` in `pipelines/stacking/subset_noise_run.py` (STK-42) <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF --> | Halves too small to reject outliers, or with the shared frame too large a part of each half. | Run the job on subsets of 16, 24 and 40 frames of one night and check that the difference noise scaled by the formula stays constant. |
| STK-D26 | Group frame noise `s_g`: clip limit around the median of neighbor differences | 4 rough (median absolute deviation) scales | code constant `NOISE_CLIP_SIGMAS` in `pipelines/stacking/processing/exposure_groups.py` <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.NOISE_CLIP_SIGMAS --> | Star pixels raise `s_g` in a crowded field (cut too wide), or real noise is cut (too narrow), which moves the group weights. | On synthetic Gaussian noise rounded to steps of 1 and 4 ADU the estimate is within 0.1% of the true value (tests in `test_exposure_groups.py`). On real frames, compare `s_g` with the noise model on star-poor and crowded fields. |
| STK-D27 | Gain and read noise at another offset: which frames may use the entry of the same gain and binning at the nearest other offset | frames with under 20% exact zeros (`CLIPPED_FRAME_ZERO_FRACTION`, [STK-22](#stk-22)) | method `CameraProfile.sensor_noise_for` in `models/camera_profile.py` <!-- impl: astrometricslib.models.camera_profile.CameraProfile.sensor_noise_for --> | The offset is assumed to change neither gain nor read noise. If it does, offset-0 frames get the wrong read noise and sky rate. | Measure the gain from flat pairs at both offsets (flats are not clipped), and compare the noise model with the measured frame noise at both offsets. |
| STK-D28 | Stack noise plate solution: member frames of the reference group tried when no reference frame is named | 5 | code constant `MEMBER_FRAME_ATTEMPTS` in `pipelines/stacking/post_processing/stack_noise.py` <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.MEMBER_FRAME_ATTEMPTS --> | Too few: the noise is not measured when the first frames have no solution. Too many: slow on a stack that cannot be solved. | Restack a group target whose reference group dropped a frame, and compare the noise measured through a member frame with the noise of the same stack after it is plate solved. |
