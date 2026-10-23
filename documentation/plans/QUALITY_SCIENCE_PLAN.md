# Quality Science Plan

This plan lists the new quality metrics the observatory needs, and how each one is defined,
measured and validated. It is the science companion of `QUALITY_METRICS_PLAN.md`. That plan
builds the shared shape: the measurement record, the metric registry, the store, the public API,
and efficiency in `wayfindinglib`. This plan fills that shape with new measurements.

The plan covers:

- how a new metric enters the system,
- the new metrics, by checkpoint, with their definitions,
- the observing values that `wayfindinglib` computes instead,
- the evidence each metric needs before anyone acts on it,
- the data that must be collected,
- the order of work, and the items left for later.

The metrics and their corrections come from the technical review board of 2026-10-17, chiefly the
observational astronomer's review, and from the second review that day (appendix A). The code is
the source of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: split from `QUALITY_METRICS_PLAN.md`, nothing built. Work starts after
phase 1 of that plan, which provides the registry and the store. The few metrics that
efficiency and the target dataset report need first stay in that plan: the corrected star width,
sky in electrons, sky noise over read noise, gain and read noise for each camera setting, and the
stack noise in empty apertures and against frame count.

Status on 2026-10-23, batch S1 (branch `94-science-s1-noise`): built and run on the M 57 scratch
stack. Section 2.3: the noise the stack's frames predict in an empty aperture, stored as a sky part,
a read part, their sum and the two numbers that convert it to the stack's units (the ratio to the
measured noise is not stored). Section 2.2: the noise the master flat adds to the same apertures,
with the stack's sky level, stored beside it. Issue #94's two replacements: the brightest star's
peak as a fraction of the ceiling for each exposure group, and the flat noise next to the stack
noise (parts only). `recommended_exposure_seconds` and the flat gate stay until #97. Evidence is in
`STK-38`, `STK-39` and `STK-40` of the validation register.

Status on 2026-10-23, aperture fix (branch `94-noise-aperture-fix`): the empty noise apertures are
now placed away from faint sources as well as bright stars, using a mask made from the stack's
pixels (never from the apertures' sums). On the M 57 library stack the measured noise fell from
3.8 to 2.2 times the model's, and the ratio of the measured noise to the model is no longer
dominated by a few faint stars. It does not reach 1: shared structure remains. Numbers are in
`STK-36` and `STK-38`; `STK-36`'s 5% growth criterion is not met on the new placement.

The second review of 2026-10-17 moved the observing values (guide error, pointing error, dew,
Moon and airmass) out of the `astrometricslib` tables into `wayfindinglib` (section 2.5). It added
the undersampling term, the spectroscopy class gap against SNR, the flat-noise check and the
photometry amplitude source. It moved each night's contribution earlier, deferred the asteroid
completeness and null search (section 7), and added two later items (section 6).

## 1. How a new metric enters

Every metric in this plan follows the rules of `QUALITY_METRICS_PLAN.md`:

1. **Declared before it is built.** The metric gets a `MetricSpec` in its pipeline's `metrics.py`:
   name, definition version, unit, estimator, sample definition, band or channel, the function
   that computes it, the kind of uncertainty and how it is found, and its register entry.
2. **Measured, not judged.** `astrometricslib` stores the raw value. A ratio stores its numerator,
   its denominator and its model separately. `wayfindinglib` decides what the value means.
3. **One estimator, one name.** A second way of measuring the same quantity gets a second name.
4. **Evidence before advice.** A metric can be stored as soon as it is built. `wayfindinglib` uses
   it in a recommendation, or in efficiency, only after its validation register entry reaches
   "Measured" or "Partly measured" (section 3).
5. **Science only in `astrometricslib`.** `astrometricslib` measures images, stacks and pipeline
   results. Values that come from the time and the pointing, or from the observatory's logs, are
   computed in `wayfindinglib` when they are read (section 2.5).

The "Decision it informs" column in the tables below is a description for readers. It matches the
descriptive `MetricSpec.decisions` field. Nothing in `astrometricslib` reads it; the decisions
themselves are rules in `wayfindinglib`.

### 1.1 What issue #93 covers

Issue #93 builds the metrics that an agreed use case in `QUALITY_USE_CASES.md` needs. The rest of
this plan stays here for later, with no ticket. The table says which use case each metric in
section 2 backs.

| Metric (section) | Backs |
| :--- | :--- |
| Catalog zero point (2.1) | UC1 transparency in the loss account; UC6 tonight's sky model |
| Zero-point scatter within a night (2.1) | UC15 "Is tonight photometric?" |
| Effective star width (2.1) | UC1 and UC6 seeing factor of efficiency |
| Target clipped (2.1) | UC2 overexposed setting |
| Center-to-corner star width (2.1) | UC10 tilt |
| Field rotation (2.1) | UC10 polar alignment |
| Flat-to-light match (2.2) | UC9 calibration gap |
| Fixed pattern left after calibration (2.2) | UC7 dither advice |
| Master flat noise contribution (2.2) | UC3 why more frames did not help |
| Noise over the expected noise (2.3) | UC3 why more frames did not help |
| Each night's contribution (2.3) | UC2 which frames to leave out |
| Walking noise (2.3) | UC7 dither advice |
| Astrometry expected residual (2.4) | UC5 trusting a result |
| Photometry noise model, scintillation share, target amplitude (2.4) | UC5 trusting a result |
| Photometry period sensitivity (2.4) | UC7 period alias recommendation |
| Spectroscopy SNR per resolution element, class gap against SNR (2.4) | UC5 trusting a result; spectroscopy finish line |

Not in #93, kept for later: background gradient (frame and stack), satellite trails and cosmic
rays, saturation and nonlinearity, limiting magnitude, rejected-pixel clustering, astrometric
residual pattern, plate scale ratio, spectral contamination and second order, the asteroid items
in section 2.4, the later items of section 6 and the deferred items of section 7.

## 2. The new metrics

"Depends on" names what must exist first. "Data" names data the observatory does not yet hold.

### 2.1 Each frame, before stacking

| Metric | Definition | Decision it informs | Depends on | Data |
| :--- | :--- | :--- | :--- | :--- |
| Catalog zero point | Instrumental magnitudes of matched stars against Gaia G, or Gaia-XP magnitudes synthesized in the frame's filter, with a fitted color term. Corrected for airmass with a fitted extinction coefficient. | Transparency, cloud | Plate solution; Gaia access | A few photometric nights to fit the extinction coefficient |
| Zero-point scatter within a night | Robust spread of the airmass-corrected zero point across the night's frames | Whether the night suits photometry. This backs the use case "Is tonight photometric?" (UC15 in `QUALITY_USE_CASES.md`). | Catalog zero point | None |
| Effective star width | The star width with the pixel's own blur added: FWHM_eff² ≈ FWHM² + c·p², with p the pixel size and c fitted on this setup. Efficiency's seeing factor uses it, and the physical reference states the pixel scale. | Undersampling; seeing loss | The corrected star width; plate scale | Frames over a range of seeing, to fit c |
| Background gradient | Size and direction of a fitted plane or low-order surface across the background, with stars masked | Moon, horizon or a light source; flats | None | None |
| Target clipped | Whether pixels of the target itself reach the saturation level, with the target located through the WCS | Exposure too long for the target | Plate solution; camera saturation level | None |
| Center-to-corner star width | Star width (the phase 2 estimator) in the center against each corner region, with the counts per region | Tilt, field curvature, collimation | The corrected star width | None |
| Field rotation | Rotation angle from registration, against time, for each frame. `wayfindinglib` groups it by guiding run and converts it to a polar-alignment error. | Polar alignment | Registration that keeps the rotation term | A night with known polar alignment, to check the conversion |
| Satellite trails and cosmic rays | Counts of long straight streaks and of single-pixel hits per frame | Frame rejection; context for the rejection map | None | None |
| Saturation and nonlinearity | Fraction of star pixels above the camera's linear range, from the camera profile's full well | Exposure and gain choice | Camera profile full well | Linearity data from flats at several levels |

For one-shot-color cameras (the Nikon D5300, the ZWO ASI120MC-S), each metric is measured per color
channel, and the channel is part of the measurement's subject.

### 2.2 Calibration health

| Metric | Definition | Decision it informs | Depends on | Data |
| :--- | :--- | :--- | :--- | :--- |
| Flat-to-light match | Correlation of dust-shadow positions between the master flat and the calibrated lights; camera rotation since the flats | Retake flats | Plate solution for the rotation | None |
| Fixed pattern left after calibration | Fine-scale noise of the calibrated, undithered frames that repeats from frame to frame | Calibration or dithering | None | Undithered frame runs |
| Master flat noise contribution | The noise the master flat adds to a calibrated stack, stored beside the stack's empty-aperture noise. A rule in `wayfindinglib` compares the two. | More flats | Master flat; empty-aperture stack noise | None |

### 2.3 After stacking

Empty-aperture stack noise and stack noise against frame count moved to `QUALITY_METRICS_PLAN.md`
(section 5.1), because the target dataset report needs them for its "take N more" recommendation.
The metrics below build on them.

| Metric | Definition | Decision it informs | Depends on | Data |
| :--- | :--- | :--- | :--- | :--- |
| Noise over the expected noise | Empty-aperture noise against the noise expected from the weighted effective frame count, read noise and sky. Stored as its parts, not only as the ratio. | More frames, dithering, calibration | Empty-aperture noise; gain and read noise | None |
| Each night's contribution | Each night's share of the stack's weight, and the stack's empty-aperture noise without it. Reuses the subset stacking that phase 3 of the main plan builds for stack noise against frame count. | Drop poor nights on purpose | Subset stacking (main plan, phase 3) | None |
| Limiting magnitude | The magnitude of 50% recovery of injected point sources, or 5 times the empty-aperture noise, with the aperture stated | Depth reached | Zero point; injection | None |
| Walking noise | Noise anisotropy along the drift direction of the frames | Dithering | Registration shifts | Undithered and dithered runs |
| Rejected-pixel clustering | A defined clustering statistic of the rejection map, separating trails from scattered rejection | Satellites, aircraft | Rejection map | None |
| Background gradient after stacking | As in section 2.1, on the stack | Flats, gradient removal | None | None |

### 2.4 After each pipeline

| Pipeline | Metric | Definition | Decision it informs | Data |
| :--- | :--- | :--- | :--- | :--- |
| Astrometry | Expected residual | About FWHM/(2.35·SNR) per star, combined with the Gaia position error propagated to the frame's epoch and the pixel-phase error from undersampling. Stored beside the measured residual. | Distortion or tilt against noise | None |
| Astrometry | Residual pattern | The fit residuals binned across the field | Distortion model | None |
| Astrometry | Plate scale ratio | Measured plate scale over the configured plate scale | Camera spacing changed | None |
| Photometry | Noise model, stored separately | Photon, sky, read and scintillation noise against magnitude. Scintillation from Young's formula with the usual empirical correction, labeled model-derived. | Systematic floor, flats, comparison stars | None |
| Photometry | Scintillation share | Scintillation's share of the bright-star noise | Longer exposures or defocus for bright targets | None |
| Photometry | Period sensitivity | The window function of the observing times, and the periods it cannot separate | Which nights to add to break an alias | None |
| Photometry | Target amplitude, with its source | The amplitude the photometry finish line is measured against. It names its source: the target's own light curve once its phase coverage is enough, otherwise the catalog amplitude (VSX), labeled as a catalog value. | When the light curve is good enough | None |
| Spectroscopy | SNR per resolution element | SNR at the classifying lines, the element taken from the measured line spread in Å | Exposure needed for a subtype | None |
| Spectroscopy | Class gap against SNR | The spectral-class gap reached at each SNR, calibrated by degrading high-SNR spectra with added noise (injection). When even the high-SNR spectra do not separate the classes, the outcome is "not reachable at this resolution". | Exposure needed for a subtype, or stop | High-SNR spectra of classified stars |
| Spectroscopy | Contamination and second order | Overlap of neighboring spectra, and of the second order for a slitless grating | Rotate the camera | None |
| Asteroid detection | Position uncertainty with timing | The detection's position error combined with the timing error times the motion rate | Whether to report to the MPC (section 6) | Timing accuracy of the capture computer's clock |
| Asteroid detection | Arc length | Time between the first and last detection | Observe again soon to link the object | None |

### 2.5 Observing values computed in wayfindinglib

These values describe the observing conditions, not the image. They are not `astrometricslib`
metrics and are not stored as measurements. `wayfindinglib` computes them when they are read, and
they stay available beside the metrics above.

| Value | Where it is computed |
| :--- | :--- |
| Guide error (RMS) for each frame | The guide-log join in `wayfindinglib` `control.history.frame_guiding` |
| Pointing error (solved center against the commanded position) | The alignment history: `control.remote.sync_frames` and `control.history.query(kind="alignment")`, from the mount logs |
| Moon separation and illumination; airmass | `wayfindinglib`'s sky engine, from the frame time and pointing |
| Dew | A rule in `wayfindinglib` that reads two metrics: a rise in the corrected star width together with a fall in the zero point over the night. A night with known dew checks the signature. |
| Polar-alignment error | Field rotation (section 2.1) grouped by guiding run and converted in `wayfindinglib` |

## 3. Evidence

Each metric gets a validation register entry before `wayfindinglib` acts on it. The entry follows
`documentation/library_design/validation/index.md`, and adds:

- **The data set**, with dates and the code version, and whether it is in-sample or held out.
- **Held out by night or by field**, never by star, because stars in one field share systematics.
- **A minimum sample size** and a confidence interval (Wilson or bootstrap). An entry may conclude
  "insufficient evidence". With one telescope and a few nights, many entries will start there.
- **Injection where labels are biased.** Detection, completeness and any probability are
  calibrated on injected signals, conditioned on brightness and amplitude. Catalog labels are only
  a cross-check, because VSX lists many variables a session cannot detect, and unlisted stars
  include unknown variables. The spectroscopy class gap is calibrated the same way, on degraded
  high-SNR spectra.
- **A model-derived label** for any value that comes from a model rather than the data, such as
  scintillation.

## 4. Data to collect

| Data | Needed for | How |
| :--- | :--- | :--- |
| Flat and bias pairs at each gain setting in use | Gain and read noise (phase 2 of the main plan), noise models | The calibration measurement of decision 7 in the main plan |
| Flats at several signal levels | Linearity and full well | A flat series from low to near saturation |
| A few clear, photometric nights | Extinction coefficient, zero-point stability | Repeated frames of one field over a wide airmass range |
| Frames over a range of seeing | The pixel term c of the effective star width | Existing frames from good and poor nights |
| High-SNR spectra of classified stars | The spectroscopy class gap against SNR | Existing spectral standards, degraded in software |
| Undithered and dithered runs of one field | Fixed pattern, walking noise, dithering advice | Two runs on the same night |
| A night with known polar alignment | Checking the field-rotation conversion in `wayfindinglib` | Polar alignment measured by the capture software's routine |

## 5. Order of work

Each metric is built in the order its evidence can be gathered, and is one pull request with its
declaration, its code, its tests and its register entry.

1. **Noise over the expected noise.** Builds on the main plan's stack-noise metrics and decides
   why more frames stopped helping: fixed pattern, calibration or correlated noise. The master
   flat noise contribution comes with it.
2. **Each night's contribution.** Built right after the subset stacking of the main plan's
   phase 3, and reuses it.
3. **Zero point and transparency.** Catalog zero point, its scatter, and the extinction
   coefficient. These complete efficiency's transparency factor in the main plan's section 4, and
   back the "Is tonight photometric?" use case.
4. **Star shape across the field.** Effective star width, center-to-corner width, field rotation,
   trailing direction from the phase 2 elongation angle.
5. **Dithering.** Fixed pattern, walking noise, and dithering advice in `wayfindinglib`.
6. **Pipeline outcomes.** The astrometry, photometry and spectroscopy metrics of section 2.4,
   including the target amplitude source and the class gap against SNR.

The later items (section 6) and the deferred items (section 7) are not in this order.

## 6. Later items

These come after the order of work. They were added on 2026-10-17. The user owns an L filter and
a spectroscopy filter, so neither item plans for more filters.

- **Spectral standard scheduling at matching airmass.** The instrument response is best derived
  from a standard star observed at nearly the same airmass as the target. Choosing when to observe
  the standard is observing logic, so the scheduling lives in `wayfindinglib`. `astrometricslib`
  supplies the response and its airmass.
- **MPC report readiness, including clock timing.** Before a detection is reported to the Minor
  Planet Center (MPC), the position uncertainty with timing (section 2.4) must be within the MPC's
  needs. That needs a measured timing accuracy for the capture computer's clock. The readiness
  check is a `wayfindinglib` rule over the stored metrics.

## 7. Deferred

These are deferred until asteroid searches are a regular part of the user's nights.

| Pipeline | Metric | Definition | Decision it informs | Data |
| :--- | :--- | :--- | :--- | :--- |
| Asteroid detection | Completeness grid | Recovery of synthetic movers injected into the individual frames as trailed point sources, over a grid of magnitude and motion rate | What the night could find | None |
| Asteroid detection | False-positive rate per night | Candidates found by a null search, with the frame order scrambled or at a non-physical rate | Trust in candidates | None |

## 8. Open questions

- **Which reference sky and seeing to use.** The physical reference in the main plan's section 4
  needs the site's dark-zenith sky brightness, a seeing percentile and the pixel scale. These come
  from the site's own data once the zero point and the effective star width exist.
- **Probabilities.** Which metric gets the first calibrated probability. Variability is the
  candidate in the main plan's decision 8.

## Appendix A. Review board changes

The second review board met on 2026-10-17. These are its findings that touched this plan.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| F1: `astrometricslib` stays science only. Guide error, pointing error, dew, Moon and airmass leave the metric tables. | Architect, with the engineer and the astronomer | Sections 1, 2.1, 2.4, 2.5 and 4 |
| F1: Field rotation stays a per-frame measurement; the polar-alignment conversion is `wayfindinglib`. | Architect | Sections 2.1 and 2.5 |
| F1: "Decision it informs" is descriptive only. | Astronomer and engineer | Section 1 |
| F7: Undersampling in the seeing factor, with c fitted. | Astronomer | Sections 2.1, 4, 5 and 8 |
| F7: Spectroscopy class gap calibrated against SNR, with "not reachable at this resolution". | Astronomer | Sections 2.4, 3 and 4 |
| F7: Master flat noise against stack noise. | Astronomer | Sections 2.2 and 5 |
| F7: Source of the photometry amplitude. | Astronomer | Section 2.4 |
| D2: "Is tonight photometric?" use case, served by zero-point scatter. | Astronomer; user decision | Sections 2.1 and 5 |
| D2: Spectral standard scheduling and MPC report readiness as later items. | Astronomer; user decision | Section 6 |
| D4: Each night's contribution right after subset stacking. | Architect; user decision | Sections 2.3 and 5 |
| D4: Asteroid completeness grid and null search deferred. | Architect; user decision | Section 7 |

No findings were rejected.

## Appendix B. Review board changes, batch S1

The board reviewed batch S1 on 2026-10-23, and all three reviewers approved it with changes. The
user made two decisions the same day. First, the brightest star's estimated peak is stored only
inside the range that a new two-exposure check supports (`STK-41`), and is not measured above it.
Second, two noise tests run in S1: single frames against the model, and the difference of two
half-stacks (`STK-38`).

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| The noise floor fitted to four nested, unscaled subsets is not supported; a fixed pattern is far too small to explain it. | Astronomer | `STK-38`, now with the two noise tests |
| Registration interpolation does not raise the noise of an aperture sum; dark current is already in the sky; normalization and debayering are left out. | Astronomer | `expected_noise.py`, `metrics.py`, `STK-38` |
| The unit check was done on one stack only. | Astronomer | Every stack stores its predicted and measured sky level |
| A peak 82 times the room lies beyond the checked range. | Astronomer; user decision | `STK-40`, `STK-41`, `exposure_saturation.py` |
| Multi-exposure stacks combine groups per second, which the model does not follow. | Engineer | Expected noise not measured for them |
| An uncertain member list was ignored. | Engineer | Expected noise assumed, with the reason |
| A camera-profile error or a non-finite blur width could stop a stack or drop its rows. | Engineer | Guards in `exposure_group_report.py` and `record_science.py` |
| Datasheet ceilings and limits were stored as measured. | Engineer | Provenance passed through |
| No tests for the joining code. | Engineer | `test_record_science.py`, `test_group_peak.py` |
| The peak values took a second path beside the group summaries. | Architect | `ExposureGroupSummary` fields |
| `adu` named two scales. | Architect | Stack-unit metrics renamed to `_stack_unit`, phase 3's included |
| A fourth copy of the full-scale constant. | Architect, with the engineer | One constant, shared with the group combine |
| Theory was only in docstrings. | Architect | Architecture paper, section 3.4.2 |
| Advice ("about 0.6 s") in the register. | Architect | Removed from `STK-40` |
| Sky level and flat fraction were labelled as decisions. | Architect | Now context |

No findings were rejected. The architect also suggested dropping the stored aperture area and
linearity limit; both stay, as inputs to the expected noise and the peak.

## Appendix C. Review board changes, batch S2

The board reviewed batch S2 on 2026-10-23, and all three reviewers approved it with changes. The
systems engineer settled every finding; the user reviews those choices in the pull request.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| A stored night spread and a stored airmass contradict decision 18 of `QUALITY_METRICS_PLAN.md`. | Architect | Both metrics dropped; the spread stays a library helper for UC15 (`PHO-16`) |
| The median over a changing star set moves when stars join or leave, and faint stars leave first under cloud. | Astronomer | A fixed star set per session and a differential frame value (`PHO-15`) |
| The bootstrap error was labelled as the absolute error and is about five times the frame-to-frame precision. | Astronomer | The frame value carries its own precision; the level's star-sample error is a separate metric |
| Choosing the band by star count follows the catalog's state. | Astronomer, with the engineer | Gaia G preferred whenever it has enough stars (`PHO-D24`) |
| The fixed aperture makes the zero point depend on star width. | Astronomer | Stated in the metric text; on M 57 the slope is not resolved (`PHO-15`) |
| Stars above the linearity limit entered the sample. | Astronomer | Cut at the camera profile's linearity limit |
| One error in the new rows would drop every photometry row of the run. | Engineer | Its own record call |
| Frames without a path were stored under "Unknown", and frames could collide. | Engineer, with the architect | Paths kept in light-curve order; a frame without one is skipped and logged |
| No test of the runner join. | Engineer | Two-session test |
| Statistics helpers in the wrong module, a duplicate MAD factor, British spelling, two wrong comments. | Architect, with the engineer | `frame_statistics.py`, `mad_std`, US spelling, comments corrected |

No findings were rejected. The night's spread now lives only in the library helper, so `PHO-16`
is evidence for that helper, not for a stored metric.
