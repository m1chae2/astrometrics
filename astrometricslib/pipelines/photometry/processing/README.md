# Photometry processing

This stage takes the raw, per-frame brightness measurements from pre-processing and turns them into a decision: is this star variable, and does its brightness show a repeating pattern?

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

The stage does three things, in this order:

1. **Differential photometry.** It divides each star's brightness by the combined brightness of a fixed set of steady comparison stars (the comparison ensemble), so changes that hit every star at once, such as thin cloud, cancel out.
2. **Variability test.** It compares each star's scatter with the scatter of constant stars of the same brightness, using three variability indices (single numbers that summarize how much a light curve wanders beyond its noise).
3. **Period search.** For a few stars, it looks for a brightness pattern that repeats at a fixed interval.

A light curve is one star's brightness against time. Flux is the light a star delivered after the sky is subtracted, in ADU per second (ADU, analog-to-digital unit, is one step of the stored pixel value). The sections below give each part in detail.

## Correcting for conditions that are not about the star

A star's raw brightness changes a little from frame to frame even when the star itself is not changing, because of clouds, changing sky darkness, or small tracking errors. To correct for this, the code divides every star's flux by a signal built from a fixed group of steady comparison stars. `comparison_ensemble.py` holds the choice of the group and the signal. `variability_analyzer.py` applies them.

### Choosing the comparison set

The code picks the comparison stars once for the whole session. It follows these steps:

1. Keeps a star as a candidate only if it has a positive flux in every usable frame and is never flagged saturated (so bright that its pixels hit the camera's maximum value). It also requires that no catalog lists the star as variable or suspected variable (SIMBAD, Gaia DR3 or VSX, read from the star's own catalog fields by `post_processing/known_variability_labels.py`). A star with no catalog answer is still a candidate.
2. Ranks the candidates by median flux, skips the brightest 2%, and takes the band down to the brightest 30%. The brightest stars come closest to saturation, where flux stops tracking brightness. The band is widened to 10 candidates when the field is small, and cut to the 100 brightest.
3. Checks that each candidate is constant. It builds an ensemble from all the other candidates and measures how far the star scatters about it, compared with the scatter its errors predict (a reduced chi-square, which is about 1 for a constant star with correct errors). It drops the worst star and repeats. It stops when no star has a reduced chi-square above `1 + 3 sqrt(2 / (N - 1))` times the larger of 1 and the set's typical value (N is the number of frames), or when 5 stars remain. The reduced chi-square of a constant star with N - 1 degrees of freedom scatters about 1 with a standard deviation of `sqrt(2 / (N - 1))`, so the limit sits 3 standard deviations above 1.
4. Keeps at most 20 stars (`maximum_comparison_stars` on `VariabilityAnalyzer`). If more are left, it keeps the 20 with the smallest errors.

A frame in which fewer than half the usual number of stars were measured is left out of the session. A star that is missing, zero or saturated in any other frame never becomes a candidate. The set therefore has the same stars in every frame. A star that fails in one frame cannot add a step to the signal.

When every candidate has CCD-equation errors (see [Uncertainties](../README.md#uncertainties)), those errors set the weights and the expected scatter. Otherwise the code estimates each star's noise from the differences between neighboring frames of its residuals (1.4826 times the median absolute deviation of the differences, divided by the square root of 2). The median absolute deviation (MAD) is the median distance of the values from their median; 1.4826 times the MAD estimates the standard deviation of normally distributed data without being pulled by a few wild points. It then publishes no normalized errors.

### The signal and the normalized flux

**The idea.** Average the comparison stars so that their shared changes (cloud, haze) remain and their individual noise averages away, and let the better-measured stars count for more.

For each comparison star the code divides its flux by its own session mean, so a bright star and a faint star count on the same scale. The signal in a frame is the inverse-variance weighted mean of those ratios: a weighted average in which each value counts in proportion to one over its error squared, so a precise star counts more than a noisy one. A star's weight is `1 / sigma^2`, with sigma its fractional error in that frame.

The signal is multiplied by the mean of the stars' session means, so it is in ADU per second and a star as bright as the average member has a normalized flux near 1. The ensemble error is `1 / sqrt(sum of weights)` (times the same scale). Equal stars average down as `1 / sqrt(N)`. A median of raw fluxes from stars of different brightness has the noise of one star instead.

A comparison star is divided by the ensemble of the other members only. Its own noise and its own changes then stay out of its divisor. Without this, a dip in a member would lose about `1 / N` of its depth.

The code drops entirely any frame whose signal is a clear outlier compared with every other frame (a 3σ clip about the median, using the MAD: a frame more than 3 robust standard deviations from the median is removed), since that likely reflects a passing cloud or a similar problem. It needs at least 5 frames to do this.

After that, it clips each star's own isolated outliers (a cosmic ray, a bad centroid): a point more than 5 robust standard deviations from the star's median. The limit of 5 is a tuned value (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)). Two or more neighboring points beyond the limit on the same side are a real change, such as a dip, and stay.

### Airmass

The code does not fit any star's own flux against airmass (airmass is a measure of how much atmosphere the light passed through; 1 straight overhead, larger toward the horizon). Dividing by the signal already removes the dimming that every star shares. A fit of a quadratic to each star's own normalized flux would also remove part of a transit or half a pulsation cycle, because airmass changes steadily during a session. `fluxesDetrended` is a copy of `fluxesNormalized`, with the errors copied too. The field exists so that the period searches read the same field in both cases.

One option turns on a correction that comes only from the comparison stars (`ensemble_airmass_correction=True` on `VariabilityAnalyzer`; it is off, and no configuration file sets it).

**What it does.** The code fits a straight line of flux against airmass to each comparison star. It divides every star by `1 + slope x (airmass - mean airmass)`, with slope the median of the other comparison stars' slopes. This removes a trend that most comparison stars share beyond what their weighted mean absorbs.

**Limits.** It cannot tell a color-dependent extinction from real variability in the comparison stars, and the stars' colors are not known here. The target's own flux is never fitted.

### Errors

Each correction carries the measurement uncertainty forward, so `fluxesNormalizedErrors` and `fluxesDetrendedErrors` stay the same length as the values they describe (neither has a unit):

- Normalizing divides a flux `F` by the ensemble level `N`. The error of the ratio is `sqrt((sigma_F / N)^2 + (F sigma_N / N^2)^2)`. Here `sigma_F` is the star's flux error and `sigma_N` is the ensemble error described above. A comparison star uses the level and error of the other members. A session whose comparison stars lack errors gets no normalized errors.
- Detrending copies the normalized errors (or divides them by the same factor as the value when the ensemble option is on).
- A frame dropped by the outlier steps takes its error and its BJD_TDB time with it, so every per-frame list keeps one entry per remaining frame.

### What the code records about the set

The analyzer's `comparison_set` holds the star ids, the candidates that failed the constancy check, the stars left out for being listed as variable, the ensemble scatter, and the number of comparison stars used in each frame. The cross-session merge copies three of these into each star's `sessionSummaries` entry: `comparisonStarIds`, `comparisonScatterMag` and `comparisonRejectedCount`. The `comparison_ensemble` gate (`post_processing/record_quality.py`) reads the whole record.

- `comparisonScatterMag` is the median over the comparison stars of `2.5 log10(1 + CV)`, in magnitudes (the astronomers' logarithmic brightness scale; a change of 0.01 mag is about 1%), where CV (the coefficient of variation) is the standard deviation of a star's normalized flux divided by its mean. A value near the error the propagated uncertainties predict means the set is constant at the level of its noise. A larger value means the errors are too small or the set still varies.
- `comparisonRejectedCount` counts the candidates the constancy check dropped plus the stars left out for being listed as variable. A large count means the field has many variable stars among its bright stars.

## Deciding which stars are variable

A star's raw scatter grows as it gets fainter, because its measurements are noisier. A rule that flags the stars with the most scatter therefore fills with faint stars. The code asks a different question: does this star scatter more than a constant star of the same brightness scatters in this field? The code lives in `variability_indices.py`. It works in four steps.

1. **Fit a noise model of the field.** The noise model is a curve of "how much a constant star of this brightness scatters in this field". Most stars in a field are constant, so the typical scatter at each brightness gives that curve.
   - *The inputs.* For each star with at least five usable points, the code takes the star's mean instrumental magnitude (`-2.5 log10` of its mean raw flux in ADU per second; larger is fainter) and its scatter in magnitudes (`1.0857 x std / mean` of the detrended flux, using the sample standard deviation; 1.0857 is 2.5 / ln 10, which turns a small fractional scatter into magnitudes).
   - *The curve.* It sorts the stars by magnitude and cuts them into bins of equal star count: at least 10 stars per bin and at most 10 bins. The median scatter of each bin gives one point of a curve. A running maximum makes the curve rise or stay level with magnitude. Between bin centers the curve is straight in magnitude against the logarithm of the scatter. Brighter than the first bin it stays level. Fainter than the last bin it continues with the slope of the last two bins and never falls.
   - *A star's expected scatter.* The expected scatter of a star is the curve at the star's magnitude, but never below the star's own median propagated error.
   - *Minimum field size.* The model needs at least 20 stars. A field with fewer stars has no model, and the code flags stars by the CV cutoff described at the end of this section.
   - *Output.* The runner records the fitted curve (bin magnitude, scatter and star count) on the run as `noiseModelCurve`, so a plot can show it next to each star's `instrumentalMag` and `rmsMag`.
2. **Compute three indices per star.** Each index catches a different sign of real change: too much scatter for the star's brightness, scatter too large for its error bars, and neighboring points that move together. They use the detrended flux and the detrended errors (or the normalized ones when the star has no detrended values).
   - **Excess scatter** (`excessScatter`, no unit). The star's measured scatter divided by its expected scatter. A constant star is near 1. A value of 3 means three times the scatter of a constant star of the same brightness.
   - **Reduced chi-square** (`reducedChiSquare`, no unit). This asks whether the points sit farther from their mean than their error bars allow. `sum(((x_i - m) / s_i)^2) / (n - 1)`, where `m` is the error-weighted mean of the values and `s_i` is the error of point `i`. A constant star whose errors are right is near 1. The errors `s_i` are the propagated errors, raised to the noise-model level when that level is higher (every error of the star is multiplied by the ratio of the model's scatter to the star's median propagated error). The propagated errors leave out centroid jitter and flat-field errors, so unscaled they give a constant bright star a chi-square far above 1. A star with no errors gets a constant error equal to the model's scatter.
   - **Stetson J** (`stetsonJ`, no unit). Stetson (1996, PASP 108, 851). This asks whether consecutive points tend to sit on the same side of the mean, as they do in a real, slow change. Each point gets the normalized residual `d_i = sqrt(n / (n - 1)) (x_i - m) / s_i`. For each pair of consecutive points the code forms `P = d_i d_(i+1)`. J is the mean over the `n - 1` pairs of `sign(P) sqrt(|P|)`. Every pair has weight 1; Stetson's weights for unevenly spaced times are not used. White noise gives products of random sign, so J stays near 0. A smooth change gives mostly positive products, so J is positive. One bad point gives two products of opposite sign, so it adds little.
   - The **coefficient of variation** (`coefficientOfVariation`, standard deviation over mean) stays on every light curve. It sets each star's `outputQuality` margin and the fallback cutoff. It is not used to choose candidates when a noise model exists.
3. **Calibrate the thresholds from the field.** Each threshold sits well above what the field's typical (mostly constant) star shows, so it adapts to the session's conditions.
   - The chi-square threshold is the field's median of `log10(chi-square)` plus 2.326 robust standard deviations (1.4826 times the median absolute deviation), turned back into a chi-square.
   - The J threshold is the field's median J plus 2.326 robust standard deviations.
   - The excess-scatter threshold is fixed at 1.5, a 50% excess (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)). It is chosen so that the ordinary spread of a constant star's measured scatter (about `1 / sqrt(2 (n - 1))`, 10 to 20% for 25 to 50 points) rarely reaches it.

   Why these choices: 2.326 is the one-sided 99th percentile of a normal distribution (99% of normal values fall below the mean plus 2.326 standard deviations). The median and the median absolute deviation barely move for the few variable stars in a field. The chi-square threshold rises when every chi-square in the field is high together, as when the camera gain is assumed.
4. **Select.** Each star gets `variabilityScore`, the smaller of `chi-square / threshold` and `excess scatter / 1.5`, capped at 1 when J does not exceed its threshold. The score is above 1 exactly when all three indices exceed their thresholds, and it ranks the stars. The `variability_discrimination` gate takes the AUC of this score.

   A score above 1 is not enough on its own, because a field of hundreds of stars will hold a few constant stars that pass by chance. Each star also gets `falseAlarmProbability`, the chance that a constant star would show at least its chi-square from noise alone. It also gets `falseDiscoveryQValue`, that probability corrected for the number of stars tested (the Benjamini and Hochberg step-up rule, which keeps the expected share of false alarms among the flagged stars below the chosen level). A star is a candidate only when its score is above 1 and its q-value is below `FALSE_DISCOVERY_LEVEL` (0.01). The photometry README gives the details of both numbers.

The 1.5, the 2.326 (the 99th percentile), the 0.01 q-value level, the bin sizes and the 20-star minimum are tuned values. Synthetic fields check that they work (partly measured; see [PHO-06](../../../../documentation/library_design/validation/photometry.md#pho-06)). One real field shows how far they are from the synthetic result (partly measured; see [PHO-08](../../../../documentation/library_design/validation/photometry.md#pho-08)).

### Why J is a veto

Stetson J ranks variables less well than the other two indices, because it uses only the sign pattern of neighboring points. Taking the smallest of the three ratios would let J pull the ranking down. The score therefore uses J only as a veto: it keeps J's protection against isolated bad points without lowering the ranking that chi-square and excess scatter give (partly measured; see [PHO-06](../../../../documentation/library_design/validation/photometry.md#pho-06)).

When the field has fewer than 20 stars with a usable light curve, the code has no noise model. It then flags the stars whose CV exceeds the field's CV cutoff, `max(0.02, median + 7.4 x MAD)` of the field's CVs, and leaves the indices empty. The `scatter_population` gate says so.

## Looking for repeating patterns

The code also searches for a period: a repeating pattern in brightness over time. It does this for the target's own star and the 10 brightest other stars with enough brightness measurements (`MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH` in `batch.py`), not for every star in the field. It searches for two different kinds of pattern:

- A smooth, repeating rise and fall in brightness, which can indicate a pulsating star or one star orbiting another.
- A brief, box-shaped dip in brightness that repeats on a regular schedule, which can indicate a transiting planet or an eclipsing binary star.

Both searches report a verdict on whether the pattern found actually stands out from noise, since chance alone can always produce some repeating pattern in any data set.

The cycle search uses the Lomb-Scargle periodogram (astropy's `LombScargle`), which fits a sine wave at each trial frequency and reports how well it fits; a periodogram is that fit strength plotted against frequency, and a real period shows as a peak. The dip search uses Box Least Squares (`BoxLeastSquares`), which slides a box-shaped dip across the folded light curve at each trial period. (Folding a light curve at a trial period stacks every cycle on top of the others: each point is plotted at its phase, its position within one cycle from 0 to 1, so a dip that repeats at that period lines up.)

The searches weight each point by its uncertainty when the star has them (`dy` in astropy's `LombScargle` and `BoxLeastSquares`). They use the star's detrended errors with the detrended brightness, or the normalized errors with the normalized brightness. If any error is missing, zero or not a number, or the list has the wrong length, the searches ignore all of them: the cycle search then weights every point equally, and the dip search uses one scatter estimated from the differences between neighboring points. The noise-only versions that set the false-alarm probability shuffle each brightness value together with its own error, so a shuffled light curve has the same weights as the real one. (A shuffled light curve has the same values in a random order, so any period in it is due to chance; the shuffled curves show how strong a peak chance alone produces.)

The cycle search samples the frequency axis about ten times per periodogram peak. A peak is about 1/T wide in frequency, where T is the time span from the first to the last point, so the step is 1/(10 T). A long span with a short minimum period can need more than 20,000 samples (`_MAXIMUM_SEARCH_GRID_POINTS`). The search then runs in two stages:

1. The code evaluates every second, third, or n-th sample of the full grid (the coarse grid), so that at most 20,000 samples remain.
2. The code finds the 20 strongest separate peaks on the coarse grid. It evaluates the full-resolution grid within three coarse steps either side of each one, and keeps the strongest point from either stage as the best period.

The noise-only versions that set the false-alarm probability go through the same two stages, so the shuffled and real results come from the same procedure. When the grid is thinned, the result's `note` gives the coarse and fine steps in peak widths (a step of 1 is one full peak width, and above 1 a sample can step over a peak). `describe_frequency_grid` in `periodicity_search.py` returns the same numbers. The box search does not use the two-stage search; it still searches its thinned period grid only.

The `holdout_cycle` check in `period_checks.py` finds the period again from the training nights. Its search window is 25% of the frequency either side of the full-data one (`_FREQUENCY_WINDOW`), and its step is 1/(5 T) for the training span T (`_SAMPLES_PER_PEAK`) (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)). If the window would need more than 20,000 points, the window narrows around the full-data frequency and the step stays the same.

The time axis is days since the first point, taken from the star's BJD_TDB times (`timeBjdTdb`, mid-exposure; a time scale that removes the light-travel effect of Earth's orbit) when it has one per timestamp, and from the UTC capture times otherwise. The held-out and alias checks in `period_checks.py` use that axis and the brightness only, not the errors.

## Long-term variability

When a target has more than one observing session, the code combines the brightness histories of each star from the different sessions into one longer record. It then measures how much the star's brightness differs from one session to another. This can reveal a variable star that changes too slowly to notice within a single session.

The measurement works on each session's median normalized flux (the star's flux divided by the comparison signal in the same frame, scaled so that an average comparison star is near 1, so it has no units). The combined record keeps each session's level as measured and does not rescale one session to match another. Rescaling would remove the very change this search looks for.

For each star with at least two sessions of at least three usable points each, the code:

1. Takes the highest and the lowest session median.
2. Converts their ratio to an amplitude in magnitudes: `2.5 log10(highest / lowest)`.
3. Divides the difference of the two medians by their combined expected error. The error of one session's median is `1.2533 x scatter / sqrt(points)` (1.2533 is sqrt(pi / 2), the factor by which the median of normally distributed values is noisier than their mean), where `scatter` is the session's within-session scatter (1.4826 times the median absolute deviation of its normalized flux). The result is the significance, in multiples of that error.
4. Flags the star when the significance is above 3 and the amplitude is at least 0.02 mag.

The code stores the amplitude and the significance on the star's light curve (`betweenSessionAmplitudeMag` and `betweenSessionSignificance`). It leaves the star's coefficient of variation unchanged, so that number stays a within-session scatter.

Each session picks its own comparison group, so two sessions can disagree about a star's level because the groups differ and not because the star changed. The code keeps a summary of each session on the light curve (`sessionSummaries`): the session id, the number of usable points, the star's median normalized flux and its scatter, the number of comparison stars (the same in every frame), the median of the comparison signal, the comparison stars' ids, their scatter in magnitudes, and the number of stars turned away. A reader can compare these across stars. A shift that every star in the field shares points to the comparison group, not to the star.

## Settings

- **Comparison stars.** The selection is checked only on synthetic light curves and drifting synthetic frames with known truth (partly measured; see [PHO-01](../../../../documentation/library_design/validation/photometry.md#pho-01)). The comparison-set evidence on a real field comes from a median-based ensemble, not the weighted one described here (partly measured; see [PHO-13](../../../../documentation/library_design/validation/photometry.md#pho-13)). No real field has checked these settings (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)):
  - the band edges, which skip the brightest 2% and reach down to the brightest 30%;
  - the minimum of 5 and the cap of 20 comparison stars;
  - the 3σ consistency limit;
  - the rule that a usable frame has at least half the typical number of measured stars.
- **Where they are set.** `maximum_comparison_stars` and `ensemble_airmass_correction` are arguments of `VariabilityAnalyzer`. No configuration file sets them.
- **Noise model and thresholds.** The sizes of the noise model (20 stars, 10 per bin, 10 bins), the excess-scatter threshold of 1.5, the 2.326 robust standard deviations and the 0.01 q-value level are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Period searches.** The 10 brightest stars searched, the frequency-grid cap of 20,000 points and the 20 refined peaks are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Between-session flag.** The flag needs a significance above 3 and an amplitude of at least 0.02 mag (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Outlier clips.** The 3σ frame clip and the 5σ per-star clip are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).

## Quality metrics

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `excessScatter` | none | 1.5 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: more scatter than a constant star of the same brightness | `variabilityScore` |
| `reducedChiSquare` | none | Field median plus 2.326 robust standard deviations, in `log10` (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: points sit farther from their mean than their error bars allow | `variabilityScore`, `falseAlarmProbability` |
| `stetsonJ` | none | Field median plus 2.326 robust standard deviations (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | Positive: neighboring points move together, as in a slow change | `variabilityScore` (as a veto) |
| `variabilityScore` | none | Above 1 when all three indices pass | High: a stronger candidate | Candidate rule; `variability_discrimination` gate |
| `falseAlarmProbability` | probability | None on its own | Low: noise alone is unlikely to give this chi-square | `falseDiscoveryQValue` |
| `falseDiscoveryQValue` | probability | Below 0.01 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | Low: the star stays flagged at a strict false discovery rate | Candidate rule |
| `coefficientOfVariation` | none | CV cutoff: field median plus 7.4 MAD, at least 0.02 (measured; see [PHO-10](../../../../documentation/library_design/validation/photometry.md#pho-10)) | High: more scatter relative to the mean | CV-only fallback rule; post-processing margin |
| `comparisonScatterMag` | mag | None | Near the predicted error: the comparison set is constant at the level of its noise. Larger: the errors are too small or the set still varies. | `comparison_ensemble` gate |
| `betweenSessionAmplitudeMag` | mag | At least 0.02 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: a larger level change between sessions | Between-session flag |
| `betweenSessionSignificance` | none | Above 3 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: the level change is large next to its expected error | Between-session flag |

## Catalog zero point

*Catalog zero point* ties the camera's counts to the catalog's brightness scale. A star's flux in ADU per second gives it an instrumental magnitude, `-2.5 log10(flux)`. The catalog gives the same star a magnitude `m`. The offset `m + 2.5 log10(flux)` would be the same for every star in a clean frame. In practice each star has its own offset (its color, a neighbor's light, an error in its catalog magnitude), and that scatter is the same in every frame of a session. Only the change from frame to frame belongs to the sky.

`catalog_zero_point.py` therefore reports a *differential zero point* for every frame of a session. It reads the raw fluxes right after the frames are measured, before frame rejection, so a cloudy frame gets a low zero point instead of vanishing. These are the rules:

1. **One catalog band per session.** A star's magnitude is Gaia G or SIMBAD V, according to the catalog that matched it. `session_zero_points` uses Gaia G when its fixed star set has at least 10 stars (`MINIMUM_STARS_FOR_ZERO_POINT`), else SIMBAD V. Stars of the other band are left out.
2. **Cuts.** A star counts in a frame when it lies within 0.5 of the half-diagonal from the frame center (`CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION`, because the frames are not flat-fielded), its flux is positive, its brightest pixel is below the camera profile's photometric linearity limit, and its flux is at least 20 times its own error (`MINIMUM_SIGNAL_TO_NOISE`). With no limit in the profile, nothing is measured.
3. **A fixed star set.** A star joins the set when it passes the cuts in at least 80% of the session's frames (`SESSION_STAR_FRAME_SHARE`). A star that drops out in a cloudy frame then cannot move the median.
4. **The frame's value.** The session level is the median of the stars' session-median offsets. The frame's value is that level plus the median, over the stars that pass in the frame, of each star's offset minus its own session median. A frame needs 10 such stars, or it has no value.
5. **Two uncertainties.** The frame-to-frame precision is the standard error of that median, `STANDARD_ERROR_OF_MEDIAN_FACTOR` (1.2533) times a robust sigma over the square root of the star count. The star-sample error of the absolute level is stored separately.
6. **A color slope, for SIMBAD V sessions only.** With at least 20 stars and a B-V range of at least 0.4 mag (`MINIMUM_STARS_FOR_COLOR_SLOPE`, `MINIMUM_COLOR_RANGE_MAG`), `differential_frame_estimates` fits the slope of offset against B-V. It stores the slope and does not apply it. Gaia G stars carry no color here.

The value has no airmass, color or aperture-correction term. The aperture is a fixed 4 px, so a blurrier frame loses light and the zero point falls with the star width. The cut values are design estimates (see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates), rows PHO-D24 and PHO-D25). The measured frame-to-frame precision and star-sample error are in the register (partly measured; see [PHO-15](../../../../documentation/library_design/validation/photometry.md#pho-15)).

The helper `zero_point_scatter` gives 1.4826 times the median absolute deviation of a session's frame zero points, with a bootstrap error. It needs 10 frames (`MINIMUM_FRAMES_FOR_SCATTER`; design estimate, see [PHO-D26 in the photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates); partly measured, see [PHO-16](../../../../documentation/library_design/validation/photometry.md#pho-16)). The pipeline does not store the spread or the airmass. The observatory side computes them when it reads the zero points. The numbers only measure. They do not say that a night was photometric.

## Noise model, schedule and target amplitude

Three modules turn a run's inputs into numbers that a reader can compare with the light curves. The `post_processing/` steps store them (see that folder's README). None of them judges the result.

**Noise by source** (`noise_budget.py`). For a star of a stated catalog magnitude (G 10, 12 and 14, `NOISE_MODEL_MAGNITUDES` in `../metrics.py`), the module gives the noise of one measurement from photon counting, the sky, the camera's read-out and scintillation (the twinkling of starlight in the air). The first three are the terms of the CCD equation that the aperture photometry already uses, so the sky term is recovered exactly from the saved flux errors. Scintillation uses Young's formula with a coefficient of 0.09, an airmass power of 1.75 and a scale height of 8000 m (`YOUNG_COEFFICIENT`, `YOUNG_AIRMASS_POWER`, `SCINTILLATION_SCALE_HEIGHT_M`; design estimate; see [PHO-D27](../../../../documentation/library_design/validation/photometry.md#design-estimates)). It needs the airmass from the frame headers and the telescope aperture. A site of unknown height is treated as sea level, which gives the largest value. The metrics are `photometry.model_noise_{photon,sky,read}_at_mag{10,12,14}_mag` (the noise of one measurement, in magnitudes) and `photometry.model_noise_scintillation_mag`. Lower means a quieter measurement. The model has been compared with the light curves of one night only (partly measured; see [PHO-17](../../../../documentation/library_design/validation/photometry.md#pho-17)).

**Window function of the frame times** (`observing_window.py`). Observing at the same hours each night makes a period and its echo one cycle per day away look alike. `strongest_window_peaks` finds the three strongest peaks of the schedule's window function (`ALIAS_PEAK_COUNT`, below 2 cycles per day, `MAXIMUM_ALIAS_FREQUENCY_PER_DAY`; design estimate; see [PHO-D29](../../../../documentation/library_design/validation/photometry.md#design-estimates)). Each peak is stored as `photometry.window_alias_<rank>_frequency_per_day` and `photometry.window_alias_<rank>_power_fraction` (0 to 1; near 1 means the run cannot separate periods that differ by that frequency). It uses the same function as the alias check in `period_checks.py`. A run of one night stores "not measured" with the reason (partly measured; see [PHO-18](../../../../documentation/library_design/validation/photometry.md#pho-18)).

**Target amplitude** (`target_amplitude.py`). `folded_amplitude` folds the target star's light curve on a period, cuts it into 10 phase bins, and returns the range of the bin medians. It needs at least 3 points in each of at least 80% of the bins (`PHASE_BIN_COUNT`, `MINIMUM_POINTS_PER_BIN`, `MINIMUM_FILLED_BIN_FRACTION`), so a partly covered cycle gives no value. Only a target whose name reads as a single star gets an amplitude; any other target is stored as not measured. The target star is the identified star that carries the target's catalog name (`find_star_named` in `../batch.py`). Its distance from the target must be within 30 arcsec (`MAXIMUM_TARGET_DISTANCE_ARCSEC`); a farther star is a cross-check failure and the match is refused. The metrics are `photometry.target_light_curve_amplitude_mag`, `photometry.target_phase_coverage_fraction` and `photometry.target_catalog_amplitude_mag`, the VSX catalog range with its band, kept apart from the light-curve value. A catalog value is often old and for another band. The fold has been tested on injected light curves only (design estimate; see [PHO-19](../../../../documentation/library_design/validation/photometry.md#pho-19)).

## Files

- `catalog_zero_point.py` finds the differential catalog zero point of every frame, with a helper for a session's spread.
- `comparison_ensemble.py` chooses the comparison stars and builds the signal.
- `variability_analyzer.py` applies the signal, drops outlier frames and points, and flags between-session changes.
- `variability_indices.py` fits the noise model and computes the three indices, the false-alarm probability and the q-value.
- `periodicity_search.py` runs the cycle and dip searches.
- `period_checks.py` runs the held-out and alias checks.
- `family_wise_correction.py` adjusts the false-alarm probability for every search made on the target.
- `noise_budget.py` gives the photon, sky, read and scintillation noise of a measurement.
- `observing_window.py` finds the strongest alias peaks of a run's observing times.
- `target_amplitude.py` folds the target's light curve and finds its amplitude.

For exact behavior, read the code.
