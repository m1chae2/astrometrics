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

Once the code corrects every star's brightness, it measures how much each star's brightness bounces around relative to its average, a standard way of comparing "noisiness" between stars of different brightness. It then compares each star's own noisiness against the typical noisiness of the whole field. The code flags a star that is noisier than the field by a wide enough margin as a possible variable star. This comparison is relative to the field's own conditions on the night, rather than to one fixed number, so noisier nights need a correspondingly bigger difference before the code flags a star.

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
