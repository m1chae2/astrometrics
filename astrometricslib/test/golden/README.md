# Golden-data regression suite (M 13)

This folder holds a regression suite that measures the real M 13 sample frames with the library's
pure-Python code and compares the results with numbers saved in `golden_values.json`. A "golden"
value is a measurement accepted as correct at a known time. If a refactor changes the measurement,
the test fails and names the number that moved. If the change is intended, the author regenerates
the JSON file, and the diff shows the reviewer exactly which measurements moved and why.

The suite checks results, not code paths. A change that keeps every unit test green can still move
a star count or a flux, and this suite catches that.

## Input data

The suite reads the frames in `documentation/notebooks/astrometrics/sample_data/M 13/`. They are
Git LFS files (Git Large File Storage keeps big files outside the normal Git history). The suite
uses:

- Five luminance lights, `M_13_Light_Luminance_019` to `023`: 3008 x 3008 pixels, 16 bits, 30 s,
  ZWO ASI533MM Pro camera at about -10 C.
- One spectroscopy light, `M_13_Light_Spectroscopy_024`: same camera and exposure, with a grating
  that spreads the light into a vertical streak.

In a checkout without `git lfs pull`, each file is a small text pointer that starts with
`version https://git-lfs.github.com/spec/v1`. The `sample_frames_present` fixture in `conftest.py`
detects this and skips every test with a message that names the file and the fix.

Stacking needs Siril and plate solving needs astrometry.net. Neither program is installed on the
machines that run this suite, so nothing that depends on them is pinned.

## Running the suite

```bash
.venv/bin/pytest astrometricslib/test/golden -q -p no:cacheprovider
```

The suite takes 35 to 50 seconds. Every test carries the `golden` marker (registered in
`pyproject.toml`), so `-m golden` selects these tests and `-m "not golden"` leaves them out.

## What is pinned

Each pinned number lives in `golden_values.json` as `section -> frame -> name -> pin`. A pin holds
`value`, `tolerance`, `tolerance_type` (`absolute` or `relative`) and `why`. The `why` string names
the change that last set the number.

| Section | Frame | Numbers | Measured by |
| --- | --- | --- | --- |
| `raw_frame_quality` | each of the 5 lights | star count, median FWHM, roundness, longest star streak, sky median, saturated pixel count, background level, background noise, saturated fraction, shift from the previous frame, number of quality flags | `QualityDiagnostics.frame_quality(kind="raw_check")`, `measure_frame_input_quality`, and a sigma-clipped standard deviation for the noise |
| `raw_frame_quality` | `batch` | median star count, median FWHM, flagged frame count | the same raw check |
| `source_detection` | `020` | source count; x, y and flux of the 10 brightest sources | `SourceDetector.detect` |
| `fwhm_top5` | `020` | median FWHM of a Gaussian fit to the 5 brightest stars | `measure_fwhm_from_data` |
| `spectral_frame` | spectroscopy `024` | whether the zero-order finder found a star, sky level, zero-order peak, peak-to-sky ratio, spectrum peak above sky, cross-streak FWHM, bands that held the streak, saturated pixels, streak tilt, tilt contrast | `find_zero_order_position`, `analyze_spectral_frame`, `SpectroscopyPipeline.measure_dispersion_trail` |
| `photometry_pre_S2` | `020` | flux (ADU per second) and saturated flag of the 10 brightest stars, apertures on whole-pixel centres | `_measure_aperture_flux` |
| `photometry_sub_pixel` | `020` | the same ten stars, apertures on the exact detected positions | `_measure_aperture_flux` |

Two terms in the table:

- FWHM (full width at half maximum) is the width of a star's profile at half its peak height, in
  pixels. A smaller value means sharper stars.
- ADU (analog-to-digital unit) is one count of the camera's output.

The raw check's FWHM comes from image moments of up to 150 stars. The `fwhm_top5` value comes from
a Gaussian fit of 5 stars. The two methods give different numbers on purpose, and each has its own
pin.

### Tolerances

`measurements.tolerance_for` chooses the tolerance from the number's name.

| Kind of number | Tolerance |
| --- | --- |
| Counts (stars, sources, saturated pixels, flags, bands) and the finder flag | exact |
| Sky and background levels (ADU) | 0.5 ADU |
| Positions (pixels), shifts and the streak tilt (degrees) | 0.05 |
| FWHM, flux, roundness, noise, saturated fraction, spectrum width, peaks, contrast | 1 percent |

A name that no rule covers raises `KeyError`, so a new number cannot enter the file with a guessed
tolerance.

### Repeatability

The source detector draws a random subsample to estimate the background, but only on its fallback
path, when the 2-D background map fails. On these 3008 x 3008 frames the map succeeds, so no random
numbers are used. Two runs gave identical source lists, and the pins use exact tolerances for
counts. A frame that sent the detector down the fallback path would make `source_count` vary from
run to run. The seed belongs in `source_detection.py`; widen the tolerance in this suite only if
that fix is not available.

### Notes on specific pins

- `zero_order_found` is pinned at 0. M 13 is a crowded cluster, so the finder in
  `group_alignment.find_zero_order_position` sees rival bright spots and returns nothing. If the
  finder improves and starts to find the cluster core, this pin moves.
- The other `spectral_frame` numbers use the fixed point `CLUSTER_CORE_ROW_COLUMN` and the
  instrument settings in `SPECTRAL_CONFIG_VALUES` (both in `measurements.py`). The settings come
  from the shipped config template, so the result does not depend on the local settings file.
  The cross-streak FWHM is large (about 43 pixels) because the zero order is an extended cluster
  and not a point star.
- Frame `020` has 934 detected bright regions, against 602 to 635 in the other four lights. The
  suite pins the value as measured and does not explain it.
- `photometry_pre_S2` reproduces the whole-pixel aperture centres that the pipeline used before
  review item S2 (sub-pixel apertures). It rounds each position before calling
  `_measure_aperture_flux`. `photometry_sub_pixel` passes the exact positions. The difference
  between the two blocks is the effect of S2 on these ten stars.

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

`--update-golden` without `--golden-why` stops with an error. After the run, read `git diff` on
`golden_values.json` and confirm that each moved number is one the change should have moved. Run
the command from the repository root with the folder path given, because pytest loads the option
from this folder's `conftest.py` only when the folder is on the command line.

## Files

- `conftest.py`: the `--update-golden` and `--golden-why` options, the `GoldenStore` class that
  compares or rewrites pins, the LFS skip fixture, and the session fixtures that load the frame
  and detect sources once.
- `measurements.py`: the functions that measure the frames, the tolerance rules, and the constants
  (frame names, cluster core position, instrument settings).
- `test_golden_m13.py`: one test per section of the JSON file.
- `golden_values.json`: the pinned numbers.

For exact behavior, read the code. The tests named above check each pinned measurement.
