# Processing

This step takes the raw, per-picture brightness measurements from pre-processing and turns them into a decision: is this star variable, and does its brightness show a repeating pattern?

## Correcting for conditions that are not about the star

A star's raw brightness changes a little from picture to picture even when the star itself is not changing, because of clouds, changing sky darkness, or small tracking errors. To correct for this, the code divides every star's flux by a signal built from a fixed group of steady comparison stars. `comparison_ensemble.py` holds the choice of the group and the signal. `variability_analyzer.py` applies them.

### Choosing the comparison set

The code picks the comparison stars once for the whole session. It follows these steps:

1. Keeps a star as a candidate only if it has a positive flux in every usable frame, is never flagged saturated, and no catalog lists it as variable or suspected variable (SIMBAD, Gaia DR3 or VSX, read from the star's own catalog fields by `post_processing/known_variability_labels.py`). A star with no catalog answer is still a candidate.
2. Ranks the candidates by median flux, skips the brightest 2 percent, and takes the band down to the brightest 30 percent. The brightest stars come closest to saturation, where flux stops tracking brightness. The band is widened to 10 candidates when the field is small, and cut to the 100 brightest.
3. Checks that each candidate is constant. It builds an ensemble from all the other candidates and measures how far the star scatters about it, compared with the scatter its errors predict (a reduced chi-square, which is about 1 for a constant star with correct errors). It drops the worst star and repeats, until no star has a reduced chi-square above `1 + 3 sqrt(2 / (N - 1))` times the larger of 1 and the set's typical value (N is the number of frames), or until 5 stars remain.
4. Keeps at most 20 stars (`maximum_comparison_stars` on `VariabilityAnalyzer`). If more are left, it keeps the 20 with the smallest errors.

A frame in which fewer than half the usual number of stars were measured is left out of the session. A star that is missing, zero or saturated in any other frame never becomes a candidate. The set therefore has the same stars in every frame. A star that fails in one frame cannot add a step to the signal.

When every candidate has CCD-equation errors (see below), those errors set the weights and the expected scatter. Otherwise the code estimates each star's noise from the differences between neighbouring frames of its residuals (1.4826 times the median absolute deviation of the differences, divided by the square root of 2). It then publishes no normalized errors.

### The signal and the normalized flux

For each comparison star the code divides its flux by its own session mean, so a bright star and a faint star count on the same scale. The signal in a frame is the inverse-variance weighted mean of those ratios. A star's weight is `1 / sigma^2`, with sigma its fractional error in that frame. The signal is multiplied by the mean of the stars' session means, so it is in ADU per second and a star as bright as the average member has a normalized flux near 1. The ensemble error is `1 / sqrt(sum of weights)` (times the same scale). Equal stars average down as `1 / sqrt(N)`. A median of raw fluxes from stars of different brightness has the noise of one star instead.

A comparison star is divided by the ensemble of the other members only. Its own noise and its own changes then stay out of its divisor. Without this, a dip in a member would lose about `1 / N` of its depth.

The code drops entirely any frame whose signal is a clear outlier compared with every other frame (a 3-sigma clip about the median, using the median absolute deviation), since that likely reflects a passing cloud or a similar problem. It needs at least 5 frames to do this. After that, it clips each star's own isolated outliers (a cosmic ray, a bad centroid): a point more than 5 robust standard deviations from the star's median. Two or more neighbouring points beyond the limit on the same side are a real change, such as a dip, and stay.

### Airmass

The code does not fit any star's own flux against airmass (airmass is a measure of how much atmosphere the light passed through). Dividing by the signal already removes the dimming that every star shares. A fit of a quadratic to each star's own normalized flux would also remove part of a transit or half a pulsation cycle, because airmass changes steadily over a night. `fluxesDetrended` is now a copy of `fluxesNormalized`, with the errors copied too, and the field stays so that the period searches need no change.

One option turns on a correction that comes only from the comparison stars (`ensemble_airmass_correction=True` on `VariabilityAnalyzer`; it is off, and no configuration file sets it yet). The code fits a straight line of flux against airmass to each comparison star. It divides every star by `1 + slope x (airmass - mean airmass)`, with slope the median of the other comparison stars' slopes. This removes a trend that most comparison stars share beyond what their weighted mean absorbs. It cannot tell a colour-dependent extinction from real variability in the comparison stars, and the stars' colours are not known here. The target's own flux is never fitted.

### Errors

Each correction carries the measurement uncertainty forward, so `fluxesNormalizedErrors` and `fluxesDetrendedErrors` stay the same length as the values they describe (neither has a unit):

- Normalizing divides a flux `F` by the ensemble level `N`. The error of the ratio is `sqrt((sigma_F / N)^2 + (F sigma_N / N^2)^2)`. Here `sigma_F` is the star's flux error and `sigma_N` is the ensemble error described above. A comparison star uses the level and error of the other members. A session whose comparison stars lack errors gets no normalized errors.
- Detrending copies the normalized errors (or divides them by the same factor as the value when the ensemble option is on).
- A frame dropped by the outlier steps takes its error and its BJD_TDB time with it, so every per-frame list keeps one entry per remaining frame.

### What the code records about the set

The analyzer's `comparison_set` holds the star ids, the candidates that failed the constancy check, the stars left out for being listed as variable, the ensemble scatter, and the number of comparison stars used in each frame. The cross-session merge copies three of these into each star's `sessionSummaries` entry: `comparisonStarIds`, `comparisonScatterMag` and `comparisonRejectedCount`. The `comparison_ensemble` gate (`post_processing/run_gates.py`) reads the whole record.

- `comparisonScatterMag` is the median over the comparison stars of `2.5 log10(1 + CV)`, in magnitudes, where CV is the standard deviation of a star's normalized flux divided by its mean. A value near the error the propagated uncertainties predict means the set is constant at the level of its noise. A larger value means the errors are too small or the set still varies.
- `comparisonRejectedCount` counts the candidates the constancy check dropped plus the stars left out for being listed as variable. A large count means the field has many variable stars among its bright stars.

### Measured and designed

Measured, on synthetic light curves and drifting synthetic frames with known truth (`test/test_comparison_ensemble_injection.py`): a 1 percent, 2 hour dip in a 6 hour run with the airmass rising from 1 to 2 comes back at 1.01 to 1.02 percent from light curves and 1.00 percent from frames. For comparison, a median of raw fluxes with a quadratic airmass fit to each star's own flux gives 0.51 to 0.53 percent from the same light curves and 0.30 percent from the frames. A 5 percent sinusoid in a candidate is dropped. The comparison scatter matches the propagated error within 20 percent. A 3 percent frame-to-frame transparency change is removed from every curve.

Designed, not measured on real fields: the 2 and 30 percent band edges, the 5-star minimum, the 20-star cap, the 3-sigma consistency limit and the half-of-typical rule for usable frames.

## Deciding which stars are variable

A star's raw scatter grows as it gets fainter, because its measurements are noisier. A rule that flags the stars with the most scatter therefore fills with faint stars. The code asks a different question: does this star scatter more than a constant star of the same brightness scatters in this field? The code lives in `variability_indices.py`. It works in four steps.

1. **Fit a noise model of the field.** For each star with at least five usable points, the code takes the star's mean instrumental magnitude (`-2.5 log10` of its mean raw flux in ADU per second) and its scatter in magnitudes (`1.0857 x std / mean` of the detrended flux, using the sample standard deviation). It sorts the stars by magnitude and cuts them into bins of equal star count: at least 10 stars per bin and at most 10 bins. The median scatter of each bin gives one point of a curve. A running maximum makes the curve rise or stay level with magnitude. Between bin centers the curve is straight in magnitude against the logarithm of the scatter. Brighter than the first bin it stays level. Fainter than the last bin it continues with the slope of the last two bins and never falls. The expected scatter of a star is the curve at the star's magnitude, but never below the star's own median propagated error. The model needs at least 20 stars. A field with fewer stars has no model, and the code flags stars by the CV cutoff described at the end of this section. The runner records the fitted curve (bin magnitude, scatter and star count) on the run as `noiseModelCurve`, so a plot can show it next to each star's `instrumentalMag` and `rmsMag`.
2. **Compute three indices per star.** They use the detrended flux and the detrended errors (or the normalized ones when the star has no detrended values).
   - **Excess scatter** (`excessScatter`, no unit). The star's measured scatter divided by its expected scatter. A constant star is near 1. A value of 3 means three times the scatter of a constant star of the same brightness.
   - **Reduced chi-square** (`reducedChiSquare`, no unit). `sum(((x_i - m) / s_i)^2) / (n - 1)`, where `m` is the error-weighted mean of the values and `s_i` is the error of point `i`. A constant star whose errors are right is near 1. The errors `s_i` are the propagated errors, raised to the noise-model level when that level is higher (every error of the star is multiplied by the ratio of the model's scatter to the star's median propagated error). The propagated errors leave out centroid jitter and flat-field errors, so unscaled they give a constant bright star a chi-square far above 1. A star with no errors gets a constant error equal to the model's scatter.
   - **Stetson J** (`stetsonJ`, no unit). Stetson (1996, PASP 108, 851). Each point gets the normalized residual `d_i = sqrt(n / (n - 1)) (x_i - m) / s_i`. For each pair of consecutive points the code forms `P = d_i d_(i+1)`. J is the mean over the `n - 1` pairs of `sign(P) sqrt(|P|)`. Every pair has weight 1; Stetson's weights for unevenly spaced times are not used. White noise gives products of random sign, so J stays near 0. A smooth change gives mostly positive products, so J is positive. One bad point gives two products of opposite sign, so it adds little.
   - The **coefficient of variation** (`coefficientOfVariation`, standard deviation over mean) stays on every light curve. It sets each star's `outputQuality` margin and the fallback cutoff. It is not used to choose candidates when a noise model exists.
3. **Calibrate the thresholds from the field.** The chi-square threshold is the field's median of `log10(chi-square)` plus 2.326 robust standard deviations (1.4826 times the median absolute deviation), turned back into a chi-square. The J threshold is the field's median J plus 2.326 robust standard deviations. 2.326 is the one-sided 99th percentile of a normal distribution. The median and the median absolute deviation barely move for the few variable stars in a field. The chi-square threshold rises when every chi-square in the field is high together, as when the camera gain is assumed. The excess-scatter threshold is fixed at 1.5.
4. **Select.** A star is a candidate when its chi-square, its excess scatter and its Stetson J all exceed their thresholds. Each star gets `variabilityScore`, the smaller of `chi-square / threshold` and `excess scatter / 1.5`, capped at 1 when J does not exceed its threshold. The score is above 1 exactly for the candidates, and it ranks the stars. The `variability_discrimination` gate takes the AUC of this score.

The thresholds are designed values: the 1.5, the 2.326 (the 99th percentile), the bin sizes and the 20-star minimum. The synthetic fields below check that they work. No real field has validated them.

### What the indices recover: measured on synthetic data

The numbers below come from `make_variability_field` (see `test/synthetic/README.md`), not from the sky. Each field holds 400 constant stars spread evenly over 6 magnitudes of brightness (scatter from 0.0035 mag at the bright end, set by an unrecorded systematic term of 0.3 percent, to 0.06 mag at the faint end, set by photon and background noise) and 48 frames. Five percent of the stars (20) carry a sinusoid (two thirds) or an eclipsing dip (one third) with an amplitude drawn log-uniformly from 0.01 to 0.3 mag, independent of brightness. Every field runs through the real comparison-star normalization, the detrending and `identify_variable_stars`. The AUC (the chance that a random injected variable gets a higher value than a random constant star; 0.5 is chance) is the mean over 24 fields, with the standard deviation between fields.

| Index | AUC (mean, sd) |
|---|---|
| Coefficient of variation | 0.835, 0.039 |
| Reduced chi-square against the propagated errors | 0.809, 0.066 |
| Reduced chi-square against the scaled errors | 0.940, 0.044 |
| Stetson J (scaled errors) | 0.879, 0.042 |
| Excess scatter | 0.942, 0.042 |
| Smallest ratio of the three indices to their thresholds | 0.880, 0.043 |
| Chosen score (chi-square and excess scatter, J as a veto) | 0.940, 0.044 |

Reading the table:

- Dividing by the noise model matters more than the choice of index. Chi-square against the propagated errors alone ranks below the CV, because the propagated errors are too small for the bright stars. Against the scaled errors it ranks with excess scatter.
- Stetson J ranks lower than chi-square here, since it uses only the sign pattern of neighbouring points. Taking the smallest of the three ratios lets J pull the ranking down to 0.880, so the score uses J only as a veto. This gives the same AUC as chi-square with excess scatter (0.940) and keeps J's protection against isolated bad points: in 8 fields where 5 percent of the points scatter five times more than the others, the rule with J flagged none of 3,040 constant stars, and the rule without J flagged 24 (0.8 percent).
- At the thresholds above, the rule flagged none of the 9,120 constant stars in the 24 fields, and 64 percent of the injected variables: 90 percent of those with an amplitude of 0.1 to 0.3 mag, 63 percent of those from 0.03 to 0.1 mag, and 40 percent of those from 0.01 to 0.03 mag; 76 percent of the sinusoids and 38 percent of the eclipsing dips. A narrow eclipse puts few points in the dip, so it is the hardest shape.
- The injected systematic term and the outlier share are the generator's choices. A real field has other error sources, so these AUCs describe the method on a field whose noise is understood, not its performance on the sky.

To reproduce the table, run `.venv/bin/python -m astrometricslib.pipelines.photometry.test.test_variability_injection`.

When the field has fewer than 20 stars with a usable light curve, the code has no noise model. It then flags the stars whose CV exceeds the field's CV cutoff, `max(0.02, median + 7.4 x MAD)` of the field's CVs, and leaves the new indices empty. The `scatter_population` gate says so.

## Looking for repeating patterns

For a star with enough brightness measurements, the code also searches for a period: a repeating pattern in brightness over time. It searches for two different kinds of pattern:

- A smooth, repeating rise and fall in brightness, which can indicate a pulsating star or one star orbiting another.
- A brief, box-shaped dip in brightness that repeats on a regular schedule, which can indicate a transiting planet or an eclipsing binary star.

Both searches report a verdict on whether the pattern found actually stands out from noise, since chance alone can always produce some repeating pattern in any data set.

The searches weight each point by its uncertainty when the star has them (`dy` in astropy's `LombScargle` and `BoxLeastSquares`). They use the star's detrended errors with the detrended brightness, or the normalized errors with the normalized brightness. If any error is missing, zero or not a number, or the list has the wrong length, the searches ignore all of them: the cycle search then weights every point equally, and the dip search uses one scatter estimated from the differences between neighboring points. The noise-only versions that set the false-alarm probability shuffle each brightness value together with its own error, so a shuffled light curve has the same weights as the real one.

The time axis is days since the first point, taken from the star's BJD_TDB times (`timeBjdTdb`, mid-exposure) when it has one per timestamp, and from the UTC capture times otherwise. The held-out and alias checks in `period_checks.py` use that axis and the brightness only, not the errors.

## Long-term variability

When a target has more than one observing session, the code combines the brightness histories of each star from the different sessions into one longer record. It then measures how much the star's brightness differs from one session to another. This can reveal a variable star that changes too slowly to notice within a single session.

The measurement works on each session's median normalized flux (the star's flux divided by the comparison signal in the same picture, scaled so that an average comparison star is near 1, so it has no units). The combined record keeps each session's level as measured and does not rescale one session to match another. Rescaling would remove the very change this search looks for.

For each star with at least two sessions of at least three usable points each, the code:

1. Takes the highest and the lowest session median.
2. Converts their ratio to an amplitude in magnitudes: `2.5 log10(highest / lowest)`.
3. Divides the difference of the two medians by their combined expected error. The error of one session's median is `1.2533 x scatter / sqrt(points)`, where `scatter` is the session's within-session scatter (1.4826 times the median absolute deviation of its normalized flux). The result is the significance, in multiples of that error.
4. Flags the star when the significance is above 3 and the amplitude is at least 0.02 mag.

The code stores the amplitude and the significance on the star's light curve (`betweenSessionAmplitudeMag` and `betweenSessionSignificance`). It leaves the star's coefficient of variation unchanged, so that number stays a within-session scatter.

Each session picks its own comparison group, so two sessions can disagree about a star's level because the groups differ and not because the star changed. The code keeps a summary of each session on the light curve (`sessionSummaries`): the session id, the number of usable points, the star's median normalized flux and its scatter, the number of comparison stars (the same in every picture), the median of the comparison signal, the comparison stars' ids, their scatter in magnitudes, and the number of stars turned away. A reader can compare these across stars. A shift that every star in the field shares points to the comparison group, not to the star.

For exact behavior, read the code.
