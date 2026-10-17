# Golden-data regression suite (M 13)

This folder holds a regression suite that measures the real sample frames of M 13 with the library's pure-Python code and compares the results with numbers saved in `golden_values.json`. A "golden" value is a measurement accepted as correct at a known time. If a refactor changes the measurement, the test fails and names the number that moved. If the change is intended, the author regenerates the JSON file, and the diff shows the reviewer exactly which measurements moved and why.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

The suite checks results, not code paths. A change that keeps every unit test green can still move a star count or a flux (the total light measured from a star), and this suite catches that.

M 13 is a globular cluster, a dense ball of hundreds of thousands of stars, listed as number 13 in the Messier catalog. Its crowded center makes it a demanding test for star detection.

## Input data

The suite reads the frames in `documentation/notebooks/astrometrics/sample_data/M 13/`. They are Git LFS files (Git Large File Storage keeps big files outside the normal Git history). The suite uses:

- Five luminance lights (light frames, that is, frames of the sky, taken through a clear luminance filter), `M_13_Light_Luminance_019` to `023`: 3008 x 3008 pixels, 16 bits, 30 s, ZWO ASI533MM Pro camera at about −10 °C.
- One spectroscopy light, `M_13_Light_Spectroscopy_024`: same camera and exposure, with a grating that spreads the light into a vertical spectrum trail. The zero order is the plain, undispersed image of the target that the grating also leaves on the frame.

In a checkout without `git lfs pull`, each file is a small text pointer that starts with `version https://git-lfs.github.com/spec/v1`. The `sample_frames_present` fixture in `conftest.py` detects this and skips every test with a message that names the file and the fix.

Stacking (aligning and averaging frames) needs Siril and plate solving (working out where in the sky a frame points) needs astrometry.net. Neither program is installed on the machines that run this suite, so nothing that depends on them is pinned.

## Running the suite

```bash
.venv/bin/pytest astrometricslib/test/golden -q -p no:cacheprovider
```

The suite takes 45 to 60 s. Every test carries the `golden` marker (registered in `pyproject.toml`), so `-m golden` selects these tests and `-m "not golden"` leaves them out.

## What is pinned

Each pinned number lives in `golden_values.json` as `section -> frame -> name -> pin`. A pin holds `value`, `tolerance`, `tolerance_type` (`absolute` or `relative`) and `why`. The `why` string names the change that last set the number.

The table uses these terms:

- FWHM (full width at half maximum) is the width of a star's profile at half its peak height, in pixels. A smaller value means sharper stars.
- ADU (analog-to-digital unit) is one count of the camera's output.
- Roundness is a star's narrow width divided by its wide width: 1 for a circle, smaller for an elongated star.
- The raw check counts stars as connected bright regions. The longest star streak is the length, in pixels, of the longest such region. A trailed star (one smeared into a short line) makes a long region; the raw check flags a frame whose longest region is far longer than the batch's typical one.
- The zero-order finder first looks for one clear point source. When that fails, it runs an extended-target search, which looks for a bright extended patch such as a cluster core instead.
- The streak tilt is the angle of the spectrum on the sensor. The tilt contrast is the typical height of the spectrum above the background, in units of the background noise. A visible spectrum scores in the hundreds; 0 means no spectrum could be followed.
- An aperture is the circle of pixels whose light is summed to measure a star. Review item S2 (in `documentation/plans/REVIEW_ACTION_PLAN.md`) places the aperture centers on the exact detected positions instead of whole pixels. `photometry_pre_S2` keeps whole-pixel centers so the effect of that change stays visible.

| Section | Frame | Numbers | Measured by |
| --- | --- | --- | --- |
| `raw_frame_quality` | each of the 5 lights | star shape and count (star count, median FWHM, roundness, longest star streak); sky and saturation (sky median, saturated pixel count, background level, background noise, saturated fraction); shift from the previous frame and the number of stars matched to measure it; number of quality flags | `QualityDiagnostics.frame_quality(kind="raw_check")` and `measure_frame_input_quality`; the noise is a sigma-clipped standard deviation (one computed after dropping values far from the rest) |
| `raw_frame_quality` | `batch` | median star count, median FWHM, flagged frame count | the same raw check |
| `source_detection` | `020` | source count; x, y and flux of the 10 brightest sources | `SourceDetector.detect` |
| `fwhm_top5` | `020` | median FWHM of a Gaussian fit to the 5 brightest stars | `measure_fwhm_from_data` |
| `spectral_frame` | spectroscopy `024` | whether the zero-order finder found a target, whether its extended-target search found it, the found row and column, sky level, zero-order peak, peak-to-sky ratio, spectrum peak above sky, cross-streak FWHM, bands that held the streak, saturated pixels, streak tilt, tilt contrast | `locate_zero_order`, `analyze_spectral_frame`, `SpectroscopyPipeline.measure_dispersion_trail` |
| `photometry_pre_S2` | `020` | flux (ADU per second) and saturated flag of the 10 brightest stars, apertures on whole-pixel centers | `_measure_aperture_flux` |
| `photometry_sub_pixel` | `020` | the same ten stars, apertures on the exact detected positions | `_measure_aperture_flux` |
| `photometry_sequence` | `019` to `023` | shift (x, y) of lights `020` to `023` from `019`, number of measured stars, number that stayed unsaturated in all five lights, median flux scatter (coefficient of variation) of those stars | `_process_single_frame_worker` |

The raw check's FWHM comes from image moments (brightness-weighted averages of position and its square over each star's pixels) of up to 150 stars. The `fwhm_top5` value comes from a Gaussian fit of 5 stars. The two methods give different numbers on purpose, and each has its own pin.

### Tolerances

`measurements.tolerance_for` chooses the tolerance from the number's name.

| Kind of number | Tolerance |
| --- | --- |
| Counts (stars, sources, matched stars, saturated pixels, flags, bands) and the two finder flags (`zero_order_found`, `zero_order_extended`) | exact |
| Sky and background levels (ADU) | 0.5 ADU |
| Positions (pixels), shifts and the streak tilt (degrees) | 0.05 |
| Zero-order row and column (pixels) | 0.5 |
| Flux scatter of a light curve (coefficient of variation, in percent) | 5% of the value (relative), not 5 percentage points |
| FWHM, flux, roundness, noise, saturated fraction, spectrum width, peaks, contrast | 1% |

A name that no rule covers raises `KeyError`, so a new number cannot enter the file with a guessed tolerance.

### Repeatability

The source detector draws a random subsample to estimate the background, but only on its fallback path, when the 2-D background map fails. On these 3008 x 3008 frames the map succeeds, so no random numbers are used. The fallback sample also uses a fixed seed (`BACKGROUND_SAMPLE_SEED` in `astrometry/pre_processing/source_detection.py`), so a frame that takes the fallback path gives the same source list on every run. The pins can therefore use exact tolerances for counts.

### Notes on specific pins

- `zero_order_found` is pinned at 1. The finder is `group_alignment.locate_zero_order`. Its point-source search sees rival bright spots in the crowded cluster and finds nothing. Its extended-target search then finds the cluster core. `zero_order_extended` (pinned at 1) records that the extended search answered, and `zero_order_row` and `zero_order_column` pin where it put the core (about row 1492, column 1488). The extended search places a target only to within a few pixels, so these two pins allow 0.5 pixel, not the 0.05 pixel of the other positions.
- The other `spectral_frame` numbers use the fixed point `CLUSTER_CORE_ROW_COLUMN` (row 1493, column 1485), not the finder's answer, and the instrument settings in `SPECTRAL_CONFIG_VALUES` (both in `measurements.py`). The settings come from the shipped config template, so the result does not depend on the local settings file. The cross-streak FWHM is large (about 43 pixels) because the zero order is an extended cluster and not a point star.
- Frame `020` has a star count (connected bright regions) of 934, against 602 to 635 in the other four lights. The suite pins the value as measured.
  - **Cause.** A faint, straight streak crosses the whole frame, from about (x, y) = (1268, 0) to (3007, 2703). It is about 4 pixels wide and adds about 50 to 70 ADU per pixel, which is too faint to form a region of its own. It lifts faint stars that lie under it just above the raw check's cut-off, and about 360 of them appear as extra small regions. The raw check's flag names the likely source of such a streak: a satellite or aircraft.
  - **Flags.** The raw check flags the frame twice, so `flag_count` is 2: once for a star count above 1.4 times the batch median (934 against 624), and once for the faint straight streak (about 3220 pixels long at 57°). It is the one flagged frame of the batch.
  - **Effect on photometry.** The pins in `photometry_sequence` are not affected, because no measured star lies within 25 pixels of the streak.
- `photometry_sequence` pins the photometry pipeline's per-frame worker, `_process_single_frame_worker`, on fixed inputs.
  - **Detection.** `SourceDetector` runs on light `019` with `VariabilityAnalyzer`'s first setting: 3σ (three times the background noise) and FWHM 4 pixels. Detections come out brightest first.
  - **Measured stars.** Detections ranked 10 to 69, at least 50 pixels from every edge, are the measured stars (58 of them). The ten brightest are skipped because they come closest to saturating.
  - **Alignment anchors.** Detections ranked 50 to 99 are the alignment anchors: `(x, y, flux)` stars the worker matches in each later light to measure how far that light is shifted from `019`. The anchor range may overlap the measured range, because the anchors only measure the shift.
  - **Fixed choices.** These two selections are fixed choices of the test. The pipeline itself measures detections from the brightest down and picks its anchors with `select_alignment_anchors` (unsaturated stars with a high signal-to-noise ratio), so this pin checks the worker, not the pipeline's star selection.
  - **Run.** Light `019` is measured in the test process, and lights `020` to `023` go through `_process_single_frame_worker` one after the other. The whole run takes about 8 s.
  - **Check.** Each light's shift agrees with the independent shift from the raw check to within 0.05 pixel (largest gap 0.043).
  - **Noise.** The gain and read noise come from the camera profile through `resolve_detector_noise`, as in the pipeline. Only the fluxes are pinned, not their errors.
- `photometry_pre_S2` reproduces whole-pixel aperture centers, the placement that review item S2 replaced (see the table terms above). It rounds each position before calling `_measure_aperture_flux`. `photometry_sub_pixel` passes the exact positions. The difference between the two blocks is the effect of S2 on these ten stars.

## Regenerating the pins

Run the suite with `--update-golden` and a reason:

```bash
.venv/bin/pytest astrometricslib/test/golden -q -p no:cacheprovider \
  --update-golden --golden-why "what changed and why the numbers should move"
```

The run compares nothing. For each group of numbers it does the following:

1. Keeps a pin whose value still matches within its tolerance, with its old `why`.
2. Rewrites a pin that moved, or is new, with the measured value and the `--golden-why` text.
3. Removes a pin that the code no longer measures.
4. Prints the list of moved pins at the end of the run.

`--update-golden` without `--golden-why` stops with an error. After the run, read `git diff` on `golden_values.json` and confirm that each moved number is one the change should have moved. Run the command from the repository root with the folder path given, because pytest loads the option from this folder's `conftest.py` only when the folder is on the command line.

## Files

- `conftest.py`: the `--update-golden` and `--golden-why` options, the `GoldenStore` class that compares or rewrites pins, the LFS skip fixture, and the session fixtures that load the frame and detect sources once.
- `measurements.py`: the functions that measure the frames, the tolerance rules, and the constants (frame names, cluster core position, instrument settings).
- `test_golden_m13.py`: one test per section of the JSON file. Together the tests check each pinned measurement.
- `golden_values.json`: the pinned numbers.

For exact behavior, read the code.
