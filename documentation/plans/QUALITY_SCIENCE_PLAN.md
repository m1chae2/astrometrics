# Quality Science Plan

This plan lists the new quality metrics the observatory needs, and how each one is defined,
measured and validated. It is the science companion of `QUALITY_METRICS_PLAN.md`. That plan
builds the shared shape: the measurement record, the metric registry, the store, the public API,
and efficiency in `wayfindinglib`. This plan fills that shape with new measurements.

The plan covers:

- how a new metric enters the system,
- the new metrics, by checkpoint, with their definitions,
- the evidence each metric needs before anyone acts on it,
- the data that must be collected,
- the order of work.

The metrics and their corrections come from the technical review board of 2026-10-17, chiefly the
observational astronomer's review. The code is the source of truth. Line numbers drift, so this
plan names files and functions instead.

## Status

Status on 2026-10-17: split from `QUALITY_METRICS_PLAN.md`, nothing built. Work starts after
phase 1 of that plan, which provides the registry and the store. The few metrics that
efficiency needs first (the corrected star width, sky in electrons, sky noise over read noise,
and gain and read noise for each camera setting) stay in that plan's phase 2.

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

## 2. The new metrics

"Depends on" names what must exist first. "Data" names data the observatory does not yet hold.

### 2.1 Each frame, before stacking

| Metric | Definition | Decision it informs | Depends on | Data |
| :--- | :--- | :--- | :--- | :--- |
| Catalog zero point | Instrumental magnitudes of matched stars against Gaia G, or Gaia-XP magnitudes synthesized in the frame's filter, with a fitted color term. Corrected for airmass with a fitted extinction coefficient. | Transparency, cloud | Plate solution; Gaia access | A few photometric nights to fit the extinction coefficient |
| Zero-point scatter within a night | Robust spread of the airmass-corrected zero point across the night's frames | Whether the night suits photometry | Catalog zero point | None |
| Background gradient | Size and direction of a fitted plane or low-order surface across the background, with stars masked | Moon, horizon or a light source; flats | None | None |
| Target clipped | Whether pixels of the target itself reach the saturation level, with the target located through the WCS | Exposure too long for the target | Plate solution; camera saturation level | None |
| Center-to-corner star width | Star width (the phase 2 estimator) in the center against each corner region, with the counts per region | Tilt, field curvature, collimation | The corrected star width | None |
| Field rotation | Rotation angle from registration, against time within a guiding run | Polar alignment | Registration that keeps the rotation term | A night with known polar alignment, to check the conversion |
| Satellite trails and cosmic rays | Counts of long straight streaks and of single-pixel hits per frame | Frame rejection; context for the rejection map | None | None |
| Saturation and nonlinearity | Fraction of star pixels above the camera's linear range, from the camera profile's full well | Exposure and gain choice | Camera profile full well | Linearity data from flats at several levels |
| Context facts | Airmass, Moon separation and illumination, guide RMS joined from the guide logs | Explaining other metrics | Frame time and pointing; guide logs | None |
| Dew | A rise in star width together with a fall in zero point, over the night | Dew heater | Corrected star width; zero point | A night with known dew, to check the signature |

For one-shot-color cameras (the Nikon D5300, the ZWO ASI120MC-S), each metric is measured per color
channel, and the channel is part of the measurement's subject.

### 2.2 Calibration health

| Metric | Definition | Decision it informs | Depends on | Data |
| :--- | :--- | :--- | :--- | :--- |
| Flat-to-light match | Correlation of dust-shadow positions between the master flat and the calibrated lights; camera rotation since the flats | Retake flats | Plate solution for the rotation | None |
| Fixed pattern left after calibration | Fine-scale noise of the calibrated, undithered frames that repeats from frame to frame | Calibration or dithering | None | Undithered frame runs |

### 2.3 After stacking

| Metric | Definition | Decision it informs | Depends on | Data |
| :--- | :--- | :--- | :--- | :--- |
| Empty-aperture noise | Noise of the stack measured in random apertures of the photometric size, placed away from sources. Registration and rejection correlate neighboring pixels, so pixel noise underestimates the real noise. | Depth reached | None | None |
| Noise over the expected noise | Empty-aperture noise against the noise expected from the weighted effective frame count, read noise and sky. Stored as its parts, not only as the ratio. | More frames, dithering, calibration | Empty-aperture noise; gain and read noise | None |
| Noise against frame count | Empty-aperture noise of subset stacks of increasing size | Whether more frames will help | Subset stacking | None |
| Limiting magnitude | The magnitude of 50% recovery of injected point sources, or 5 times the empty-aperture noise, with the aperture stated | Depth reached | Zero point; injection | None |
| Walking noise | Noise anisotropy along the drift direction of the frames | Dithering | Registration shifts | Undithered and dithered runs |
| Rejected-pixel clustering | A defined clustering statistic of the rejection map, separating trails from scattered rejection | Satellites, aircraft | Rejection map | None |
| Background gradient after stacking | As in section 2.1, on the stack | Flats, gradient removal | None | None |
| Each night's contribution | Each night's share of the stack's weight, and the stack's empty-aperture noise without it | Drop poor nights on purpose | Subset stacking | None |

### 2.4 After each pipeline

| Pipeline | Metric | Definition | Decision it informs | Data |
| :--- | :--- | :--- | :--- | :--- |
| Astrometry | Expected residual | About FWHM/(2.35·SNR) per star, combined with the Gaia position error propagated to the frame's epoch and the pixel-phase error from undersampling. Stored beside the measured residual. | Distortion or tilt against noise | None |
| Astrometry | Residual pattern | The fit residuals binned across the field | Distortion model | None |
| Astrometry | Plate scale ratio | Measured plate scale over the configured plate scale | Camera spacing changed | None |
| Astrometry | Pointing error | Solved center against the commanded position | Mount model | Commanded positions from the mount logs |
| Photometry | Noise model, stored separately | Photon, sky, read and scintillation noise against magnitude. Scintillation from Young's formula with the usual empirical correction, labeled model-derived. | Systematic floor, flats, comparison stars | None |
| Photometry | Scintillation share | Scintillation's share of the bright-star noise | Longer exposures or defocus for bright targets | None |
| Photometry | Period sensitivity | The window function of the observing times, and the periods it cannot separate | Which nights to add to break an alias | None |
| Spectroscopy | SNR per resolution element | SNR at the classifying lines, the element taken from the measured line spread in Å | Exposure needed for a subtype | None |
| Spectroscopy | Contamination and second order | Overlap of neighboring spectra, and of the second order for a slitless grating | Rotate the camera | None |
| Asteroid detection | Completeness grid | Recovery of synthetic movers injected into the individual frames as trailed point sources, over a grid of magnitude and motion rate | What the night could find | None |
| Asteroid detection | False-positive rate per night | Candidates found by a null search, with the frame order scrambled or at a non-physical rate | Trust in candidates | None |
| Asteroid detection | Position uncertainty with timing | The detection's position error combined with the timing error times the motion rate | Whether to report to the MPC | Timing accuracy of the capture computer's clock |
| Asteroid detection | Arc length | Time between the first and last detection | Observe again soon to link the object | None |

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
  include unknown variables.
- **A model-derived label** for any value that comes from a model rather than the data, such as
  scintillation.

## 4. Data to collect

| Data | Needed for | How |
| :--- | :--- | :--- |
| Flat and bias pairs at each gain setting in use | Gain and read noise (phase 2 of the main plan), noise models | The calibration measurement of decision 7 in the main plan |
| Flats at several signal levels | Linearity and full well | A flat series from low to near saturation |
| A few clear, photometric nights | Extinction coefficient, zero-point stability | Repeated frames of one field over a wide airmass range |
| Undithered and dithered runs of one field | Fixed pattern, walking noise, dithering advice | Two runs on the same night |
| A night with known polar alignment | Field rotation conversion | Polar alignment measured by the capture software's routine |
| Mount logs with commanded positions | Pointing error | Kept with the session logs |

## 5. Order of work

Each metric is built in the order its evidence can be gathered, and is one pull request with its
declaration, its code, its tests and its register entry.

1. **Calibration and stack noise.** Empty-aperture noise, noise over the expected noise, noise
   against frame count. These use data already held and decide the most common question: will more
   frames help?
2. **Zero point and transparency.** Catalog zero point, its scatter, and the extinction
   coefficient. These complete efficiency's transparency factor in the main plan's section 4.
3. **Star shape across the field.** Center-to-corner width, field rotation, trailing direction
   from the phase 2 elongation angle.
4. **Dithering.** Fixed pattern, walking noise, and dithering advice in `wayfindinglib`.
5. **Pipeline outcomes.** The astrometry, photometry and spectroscopy metrics of section 2.4.
6. **Asteroid completeness and false positives.** The injection grid and the null search.

## 6. Open questions

- **Which reference sky and seeing to use.** The physical reference in the main plan's section 4
  needs the site's dark-zenith sky brightness and a seeing percentile. Both come from the site's own
  data once the zero point and the corrected star width exist.
- **Probabilities.** Which metric gets the first calibrated probability. Variability is the
  candidate in the main plan's decision 8.
