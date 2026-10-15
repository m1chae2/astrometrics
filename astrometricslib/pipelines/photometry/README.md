# Photometry

This part of the code measures how bright stars are in a series of pictures, and checks whether any of them change brightness over time. That second part is called "variability," and a star that changes brightness in a real way is called a variable star.

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This README also defines each term the first time it appears.

## In plain words

- **Differential photometry.** Photometry is the measurement of a star's brightness. A single star's measured brightness changes from picture to picture for reasons that have nothing to do with the star: thin cloud, haze, small tracking errors. Those changes hit every star in the picture at once. Differential photometry divides each star's brightness by the brightness of other stars in the same picture, so the shared changes cancel and the star's own changes remain.
- **The comparison ensemble.** The stars used for that division form the comparison ensemble (or comparison set): up to 20 steady, well-measured stars per observing session. Their combined, weighted brightness is the yardstick for every other star.
- **The light curve.** A light curve is one star's brightness listed against time, one point per picture. Each point carries an error bar (its 1-sigma uncertainty, the size of the random scatter expected from one measurement to the next).
- **Variability indices.** A variability index is one number that summarizes how much a light curve wanders, compared with what noise alone would give. The pipeline uses three of them and flags a star only when all three agree.
- **Period search.** A period search looks for a pattern that repeats at a fixed interval, such as a pulsating star or the regular dip of an eclipse.

Why it is hard: faint stars are noisy, so a rule that simply picks the stars with the most scatter picks mostly faint stars. A field also holds hundreds or thousands of stars, so some constant stars will look variable by chance. The pipeline therefore compares each star with constant stars of the same brightness, and corrects its false-alarm chance for the number of stars tested.

## What the pipeline does, step by step

1. **Collect the pictures.** The pipeline looks at every picture taken of a target and groups them into observing sessions. A session here means the pictures of the target taken on one observing night (noon to noon, local time) with the same camera gain setting and offset (the offset is the camera's constant added to every pixel so that noise below zero is still recorded). A change of filter or exposure time does not start a new session (`pipelines/shared/target_sessions.py`). The pipeline leaves out pictures taken through a spectroscopy filter, since measuring a star's total brightness does not make sense on a dispersed spectrum image (a picture where each star is spread into a rainbow streak).
2. **Measure each star's brightness, frame by frame.** This step lives in `pre_processing/`.
   - *Find the stars.* For each session, the pipeline uses the first picture to find every star in the field.
   - *Line up each picture.* It then compares every later picture in that session against the first one. It moves each star by the picture's alignment: a shift, a rotation and a change of scale, measured on up to 50 bright, unsaturated stars, not on noise peaks. (A saturated star is one so bright that its pixels hit the camera's maximum value, so its true brightness is lost.)
   - *Measure the light.* It re-centers each star on its own brightness-weighted centroid (the average pixel position, weighted by pixel brightness). It measures how much light the star collected in a circle at that exact position (the aperture), and records that as one point in the star's brightness history. Each point also gets its 1-sigma uncertainty (see "Uncertainties" below).
   - *Time-stamp the point.* The time of that point starts from the picture's `DATE-OBS` header (the capture time written into the image file) and is converted to the middle of the exposure in BJD_TDB, a time scale that removes the effect of Earth's motion around the Sun (see "Times" below). A picture with no readable `DATE-OBS` is rejected, not given the current time.
3. **Compare stars against each other.** A single star's raw brightness bounces around from picture to picture for reasons that have nothing to do with the star itself: clouds, changing air quality, small changes in tracking. To remove that noise, the pipeline picks one fixed group of up to 20 steady comparison stars per session. They are bright but not the very brightest, unsaturated in every picture, not listed as variable by a catalog, and checked for constancy. The pipeline builds a weighted average of their brightness for each picture and divides every star's brightness by it. It then drops any frame that still looks wrong after that correction. It does not fit a star's own brightness against airmass (how much atmosphere the light passed through; 1 straight overhead, larger toward the horizon). This step lives in `processing/` (`comparison_ensemble.py` chooses the group).
4. **Decide which stars are actually variable.** Once brightness is corrected, the pipeline fits a noise model of the field: the measured curve of how much scatter a constant star of each brightness shows in this field. (In this README "noise model" always means that measured curve. The noise the camera's physics predicts for one measurement is called the "predicted noise"; see "Uncertainties".) It then measures three indices per star:
   - excess scatter over the model (how many times noisier the star is than a constant star of the same brightness);
   - reduced chi-square against the star's errors (the average squared distance of the points from their mean, in units of their error bars; about 1 for a constant star);
   - the Stetson J index of correlated change (positive when neighbouring points tend to move in the same direction, as they do in a real slow change).

   It combines the three into one variability score, which is above 1 only when all three pass their thresholds. It flags a star as a possible variable when its score is above 1 and its false discovery q-value is below 0.01. The q-value is the expected share of false alarms among all stars flagged at that level (see "False alarm probability" below). This also lives in `processing/`, which gives the definitions, the thresholds and the measured AUC (the chance that a real variable gets a higher score than a constant star; 0.5 is chance, 1 is perfect).
5. **Look for repeating patterns.** A period is a repeating pattern in brightness over time, such as an eclipsing binary star (two stars that pass in front of each other) or a transiting planet (a planet that crosses its star's face). The pipeline searches for a period in only a few stars: the target's own star and the 10 brightest other stars with enough data points (`MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH` in `batch.py`). Each search takes several seconds, so a field of thousands of stars cannot all be searched; any other star can be searched on request. The searches weight each point by its uncertainty and measure time in BJD_TDB days when the star has them. This also lives in `processing/`.
6. **Judge how much to trust the results.** Two separate quality checks run alongside the steps above. One checks how good the raw data behind a star's brightness history was (`pre_processing/`). The other records how far each star's CV (coefficient of variation, its standard deviation over its mean) sits from the field's CV cutoff (a fallback variable-star boundary, defined under "The gate record"), and builds the run's gates (`post_processing/`; its README explains when that margin describes the actual variable-star decision).
7. **Combine sessions.** When a target has more than one observing session, the pipeline matches up the same stars between sessions and combines their brightness histories into one longer record (partly measured on M 81; see [PHO-14](../../../documentation/library_design/validation/photometry.md#pho-14)). The combined record keeps each session's brightness level as measured. It does not rescale one session to match another. The pipeline then measures how much each star's level differs between sessions (see "Change between sessions" below), which can reveal slower changes that a single session would miss.

## Where to look

- `pre_processing/` — turning a raw picture into one brightness measurement per star, and judging how good that raw measurement was.
- `processing/` — comparing stars against each other, deciding which ones are variable, and searching for repeating patterns.
- `post_processing/` — judging how much to trust a variability result.
- `runner.py` and `batch.py` — the code that runs the steps above in order, for one target, and combines the results.

See each subfolder's own README for more detail on that step.

## The gate record

A gate is a yes-or-no check on a whole run, as opposed to the quality records kept for each star. Gates catch problems that affect every star at once, such as lost tracking or a poor comparison set.

Besides each star's own quality records, a run keeps one record per run-level check in the summary's `gates` (built in `post_processing/run_gates.py`). Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. The tests in `test/post_processing/test_run_gates.py` give every gate input that must fail it.

A few terms used in the table:

- *Plate-solved*: matched to a star catalog, so each pixel has a known sky position.
- *Magnitude (mag)*: the astronomers' logarithmic brightness scale, `-2.5 log10(flux)`. A larger magnitude is fainter, and a change of 0.01 mag is about 1 percent in brightness.
- *CV* (coefficient of variation): a light curve's standard deviation divided by its mean.
- *MAD* (median absolute deviation): the median distance of a set of values from their median, a measure of spread that a few wild values cannot inflate.
- *CV cutoff*: the fallback rule for fields too small for a noise model (fewer than 20 usable stars). A star is flagged when its CV is above the field's median CV plus 7.4 times the MAD of the field's CVs, and never below 0.02 (see "The CV-only flag on the library").
- *Variability score*: one number per star built from the three indices (see `processing/README.md`, "Deciding which stars are variable"). It is above 1 only when all three indices pass their thresholds.
- *Anchors*: the up to 50 bright, unsaturated stars whose positions give each picture's alignment. The current rule picks them by brightness above the sky noise; the older rule, kept only as a fallback for sparse fields, took the stars ranked 50 to 100 by brightness (see `pre_processing/README.md`, "Alignment anchors").
- *Percentile*: the 95th percentile is the value that 95 percent of the measurements fall below.
- *ADU* (analog-to-digital unit): one step of the stored pixel value.

| Gate | Fails when | Not checked when |
|---|---|---|
| `ensemble_frame_rejection` | At least five frames were rejected as outliers and they are at least a quarter of all frames | Fewer than five frames |
| `capture_timestamps` | A frame has no usable capture time: none on record, or its `DATE-OBS` header is missing or unreadable (the detail lists the first three reasons) | Never. The detail also names the time scale of the light curves (see "Times") |
| `session_content` | A session produced no light curves | The run had no sessions |
| `session_plate_solve` | A session could not be plate-solved for cross-session matching | There is only one session |
| `photometry_work` | The run found nothing to do (the reason is given) | Never |
| `comparison_ensemble` | A session's comparison set has fewer than five stars, or the number of comparison stars differs from one frame of a session to another (the set must be fixed), or the comparison stars' own light curves scatter more than 5 times what their errors predict (`MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED`; the measured value is then the ratio). The detail on a pass gives the set sizes, says each set is the same in every frame, and quotes the ensemble scatter in magnitudes next to the scatter the errors predict | No session was normalized |
| `registration_drift` | Any of three limits is passed: lost tracking, a large rotation or scale change, or too many refused centroids (see "The registration drift gate" below) | No star recorded its drift and no session has a per-star summary |
| `scatter_population` | Never | Fewer than ten stars have a measured scatter, or fewer than twenty can be used to fit the noise model, so the CV cutoff stands in for the noise model. A pass quotes the number of stars the model was fitted from |
| `variability_discrimination` | Among the field's stars with a light curve, the variability score of those the catalogs list as variable does not rank above that of the others with an AUC of at least 0.7. 0.7 is a design choice (`MINIMUM_DISCRIMINATION_AUC`), also checked against the chance level at the 5% level | Fewer than 10 catalogued variables or 30 unlisted stars in the field |
| `detectable_amplitude` | The gate estimates the smallest variable this run could flag, and fails when that estimate is above 0.3 mag peak to peak (`MAXIMUM_DETECTABLE_AMPLITUDE_MAG`). The estimate is for a sinusoid on a star of median brightness. With a noise model, it is the model's scatter there times `2 sqrt(2) sqrt(1.5^2 - 1)`. Without one, it is the sinusoid whose scatter equals the CV cutoff (see the note below the table) | The run has no noise model and no cutoff |
| `flux_uncertainty` | Never. It passes with the median per-point error in magnitudes as its measured value | No light curve carries errors, or the camera's gain is unknown and the errors assume 1 electron per ADU (the detail still quotes the median error) |

`registration_drift` and `comparison_ensemble` report a lost-tracking night and a thin or unstable comparison set at the level of the run. The limit for `scatter_population` and the 7.4 multiplier in the CV cutoff are not yet backed by a measured false-alarm rate (the cutoff's behaviour on the library is measured; see [PHO-10](../../../documentation/library_design/validation/photometry.md#pho-10)).

**The `detectable_amplitude` estimate.** A sinusoid of half-height `a` adds a scatter of `a / sqrt(2)` (its root-mean-square value), and independent scatters add in quadrature (as the square root of the sum of squares). The candidate rule needs a star's total scatter to reach 1.5 times the noise model's scatter `s`, so `a / sqrt(2) = s sqrt(1.5^2 - 1)`, and the peak-to-peak size `2a` is `2 sqrt(2) sqrt(1.5^2 - 1) s`. A real star also has to pass the other two indices, so it needs slightly more.

**Two different numbers.** The 0.3 mag limit is a design estimate, not a measurement: pulsating, rotating and young variable stars mostly change by less than about 0.3 mag, while eclipsing binaries, Cepheids, RR Lyrae stars and Miras change by more, so a run that cannot see 0.3 mag is blind to most variables. It is a pass-or-fail line for the run's computed estimate. The size of signal the rule actually finds on real light curves is a separate, measured number (partly measured; see [PHO-09](../../../documentation/library_design/validation/photometry.md#pho-09)).

### The registration drift gate

Registration is the lining up of each picture with the first one. This gate checks that the lining up worked well enough for the apertures to sit on the stars.

**What it checks.** The gate fails when any of three limits is passed:

1. Frame alignment drifted more than 20 pixels, so tracking was probably lost.
2. The 95th percentile, over the pictures, of how far the fitted rotation and scale change move a corner exceeds 1 pixel (`MAXIMUM_CORNER_DISPLACEMENT_PX`). The pictures are lined up with that rotation, so the apertures follow the stars, but a star lands on different pixels in each picture and the flat-field error adds scatter. (A flat field is the correction for the uneven sensitivity of the sensor's pixels; its small leftover error differs from pixel to pixel.)
3. More than 20 percent of the star measurements kept the aligned position because the star's own centroid was refused (`MAXIMUM_CENTROID_FALLBACK_FRACTION`).

**What it reports.** The detail reports the global drift, the corner displacement, the median and 95th-percentile per-star offset (reported, not judged: it is dominated by centroid noise on faint and crowded stars), the fallback share, the worst picture, and, when a session fell back to the older anchor rule, that fact. With several sessions, it reports the worst one. The measured value is the global drift for limit 1, the corner displacement for limit 2 and the fallback share (0 to 1) for limit 3.

**Status.** The limits are design estimates, not measured false-alarm rates (partly measured on three real fields; see [PHO-04](../../../documentation/library_design/validation/photometry.md#pho-04)).

## Uncertainties

Every flux (the light a star delivered, after the sky is subtracted) comes with a 1-sigma uncertainty (one standard deviation), so a later step can weight a point by how well it was measured.

- `fluxErrors` goes with `fluxes`, in ADU per second.
- `fluxesNormalizedErrors` goes with `fluxesNormalized`. It has no unit. It combines the star's own error with the error of the comparison-star level it was divided by (`1 / sqrt(sum of weights)` for the weighted mean).
- `fluxesDetrendedErrors` goes with `fluxesDetrended`. It has no unit. `fluxesDetrended` equals `fluxesNormalized`, and its errors equal the normalized errors, unless the optional ensemble airmass correction is on (see `processing/README.md`). The pipeline never fits a star's own flux against airmass.

### Where the error comes from

A camera's noise has a few known sources: the random arrival of light (photon or shot noise, which grows as the square root of the count), the electronics that read each pixel (read noise), and the sky glow under the star. The CCD equation (the noise budget of a camera sensor) adds them up. It gives the variance (the error squared) of the flux, in electrons:

`variance = F + n_pix (S + RN^2 + D) + (n_pix^2 / n_sky) (S + RN^2)`

`F` is the star's net counts, `S` the sky counts per pixel, `RN` the read noise, `D` the dark current per pixel (signal the sensor makes by itself from heat), `n_pix` the number of pixels in the circle, and `n_sky` the number of pixels in the sky ring. All of these are in electrons except the pixel counts. The code converts the result back to ADU with the camera's gain (electrons per ADU) and divides by the exposure time, the same way it stores the flux. Dark current is taken as zero, and the equation assumes the picture has had its bias level (the fixed offset the camera adds) removed.

### Gain and read noise

**Where the numbers come from.** The gain and read noise come from the camera's profile (`gain_e_per_adu` and `read_noise_e` in `models/camera_profile.py`), then from the picture's `EGAIN` and `RDNOISE` header cards. The `GAIN` card is never used, because many cameras write a gain setting there and not electrons per ADU.

**When nothing gives them.** The code assumes 1 electron per ADU and no read noise, and the light curve records that in `errorsAssumeUnitGain` and `errorsAssumeZeroReadNoise`. With a wrong gain the errors have the right shape but the wrong size. The `flux_uncertainty` gate reports the median error in magnitudes (`1.0857 x error / flux`; 1.0857 is 2.5 / ln 10, which turns a small fractional error into magnitudes) and whether the gain was assumed.

**The ASI 533MM Pro's numbers.** The camera's profile and the shipped example config hold a gain of 0.783 e-/ADU and a read noise of 3.8 e- (partly measured; see [PHO-03](../../../documentation/library_design/validation/photometry.md#pho-03)). The gain is in electrons per stored ADU. The sensor is 14-bit and the files are 16-bit in steps of 4, so it is a quarter of the datasheet's value in sensor ADU. A gain of 3.6 e-/ADU, a datasheet figure for the sensor's own 14-bit ADU, is wrong for these files and makes the error bars about 2 times too small.

**The 5 times limit.** `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` is a design value set between clear and cloudy real fields (partly measured; see [PHO-02](../../../documentation/library_design/validation/photometry.md#pho-02)). A ratio far above it means patchy cloud or tracking loss, which dividing by the ensemble cannot remove. On clear nights the predicted errors still run somewhat smaller than the measured scatter, because the CCD equation leaves out flat-field residuals and centroid error (see [PHO-02](../../../documentation/library_design/validation/photometry.md#pho-02)).

A light curve saved before the errors existed has empty error lists. The searches then work as they did before.

## Times

`timestamps` keeps the moment the shutter opened, in UTC, as written in `DATE-OBS`. `timeBjdTdb` holds, for each timestamp, the middle of the exposure as a Julian Date (a running count of days) in BJD_TDB (Barycentric Julian Date in Barycentric Dynamical Time), in days.

Why it matters: light from a star reaches Earth earlier or later depending on where Earth is in its orbit. BJD_TDB takes the time light would arrive at the center of mass of the solar system (the barycenter), so the date of an event does not depend on where Earth is in its orbit. The shift is up to about 8 minutes, and a 300 s exposure shifts the time by another 150 s. `pre_processing/observation_times.py` does the conversion with astropy.

The conversion needs two inputs:

- The sky position of the target: the target's own right ascension and declination (the sky's longitude and latitude), or, when the target has none, the reference point of the session's reference-frame plate solution. One position serves the whole field, which keeps the error under a few seconds for a field of about one degree.
- The observatory, from `latitude`, `longitude` and `elevation` in the `[Observatory.Location]` section of the configuration. Without them, the code takes the observer at Earth's center, which changes each time by under 25 ms, and `timeBasis` says so.

`timeBasis` is one of three sentences: `BJD_TDB, mid-exposure`, the same with "observer taken at Earth's center (no site configured)", or `UTC start, no BJD_TDB (target position unknown)`, where `timeBjdTdb` is empty. The `capture_timestamps` gate quotes the run's least exact basis. The period searches use `timeBjdTdb` when a star has one value per timestamp, and the UTC timestamps otherwise.

## Change between sessions

Within a session, a star's normalized flux is its flux divided by a weighted mean of the session's comparison stars, so it has no units (the exact definition is in `processing/README.md`). The pipeline picks the comparison stars separately for each session, and each session keeps the same stars for every picture. Two sessions can therefore use different comparison stars, and a difference in a star's level between them can come from the comparison stars and not from the star.

For that reason, the merge across sessions (`batch.py`, `_merge_light_curves`) does not rescale any flux. It joins the sessions in time order, for both `fluxesNormalized` and `fluxesDetrended`. The airmass detrend runs on one session at a time and keeps the session's mean level, so the joined detrended values keep each session's trend removal and each session's level.

The merge joins `fluxErrors`, `fluxesNormalizedErrors`, `fluxesDetrendedErrors` and `timeBjdTdb` the same way. An array that only one session has is dropped, because it cannot be paired with the merged timestamps. The merged flags say "assumed" if either session assumed.

The merge also records one `sessionSummaries` entry per session on the light curve. MAD below is the median absolute deviation (the median distance of the values from their median); 1.4826 times the MAD estimates the standard deviation for normally distributed data, without being pulled by a few wild points.

| Field | Meaning |
|---|---|
| `sessionId` | The session the entry describes |
| `pointCount` | Usable (positive) normalized points in the session |
| `medianNormalizedFlux` | The star's median normalized flux in the session (no units) |
| `normalizedFluxScatter` | 1.4826 times the median absolute deviation of the star's normalized flux in the session (no units) |
| `comparisonStarCount` | The number of comparison stars per picture (the same for every picture) |
| `ensembleMedianFlux` | The median, over pictures, of the comparison signal (ADU per second) |
| `comparisonStarIds` | The ids of the session's comparison stars |
| `comparisonScatterMag` | The scatter of the comparison stars' own normalized light curves, in magnitudes (median over the set of `2.5 log10(1 + CV)`) |
| `comparisonRejectedCount` | The candidates the constancy check dropped plus the stars left out for being listed as variable |

`identify_long_term_variable_candidates` (`processing/variability_analyzer.py`) reads these entries. It asks two questions: how big is the change between sessions, and is it bigger than the noise could explain? For a star with at least two sessions of three or more points, it computes:

- the amplitude, `2.5 log10(highest session median / lowest session median)`, in magnitudes (`betweenSessionAmplitudeMag`);
- the significance, the difference of those two medians divided by their combined expected error (`betweenSessionSignificance`, no units). A significance of 3 means the difference is three times its expected error. The expected error of one median is `1.2533 x normalizedFluxScatter / sqrt(pointCount)`; 1.2533 is sqrt(pi / 2), the factor by which the median of normally distributed values is noisier than their mean.

It flags the star when the significance is above 3 and the amplitude is at least 0.02 mag. The within-session scatter (the coefficient of variation) is not changed by this search.

How to read a flag: it means that the star's level differs between sessions by more than its own scatter explains. It does not rule out the comparison stars as the cause. Compare the amplitude with the amplitudes of the other stars in the field, and with the `comparisonStarCount`, `comparisonStarIds`, `comparisonScatterMag` and `ensembleMedianFlux` of each session, before treating a flag as a variable star.

No run gate checks this amplitude yet. The `detectable_amplitude` gate in the table above describes only the within-session cutoff, so a run can pass it and still be unable to see a small change between sessions.

## Comparison stars: measured and designed

Measured only on synthetic sessions with known truth (partly measured; see [PHO-01](../../../documentation/library_design/validation/photometry.md#pho-01)).

Designed (no real field has checked them): the brightness band for comparison candidates (ranked by brightness, the brightest 2 percent are skipped and the band reaches down to the brightest 30 percent), the minimum of 5 and the cap of 20 comparison stars, the 3-sigma consistency limit, and the rule that a usable frame has at least half the typical number of measured stars. The cap and the optional ensemble airmass correction are arguments of `VariabilityAnalyzer`; no configuration file sets them.

## Known variables

Each variable candidate carries `knownVariability` and a one-sentence `knownVariabilityNote`, filled in from the saved catalog row by `post_processing/known_variability_labels.py`: whether SIMBAD, Gaia DR3 or VSX (whichever have been asked) already list the star as variable. (SIMBAD is a general star database, Gaia DR3 is the third data release of the Gaia satellite survey, and VSX is the International Variable Star Index.) A candidate the catalogs list is not a discovery. `unknown` means no catalog was asked about the star, which is not the same as `not_listed_as_variable`; the note names the catalogs that were consulted and the ones that were not.

## What the variable-star flag can see

The flag picks candidates with the noise model and the three indices described in `processing/README.md`. The score is checked on synthetic fields with known variables (partly measured; see [PHO-06](../../../documentation/library_design/validation/photometry.md#pho-06)) and on one real field (see [PHO-08](../../../documentation/library_design/validation/photometry.md#pho-08)). The `variability_discrimination` gate fails a run whose AUC on the catalogued variables is below 0.7. No AUC for the new score on the whole library has been measured yet.

Each light curve carries the indices as `instrumentalMag`, `rmsMag`, `excessScatter`, `reducedChiSquare`, `stetsonJ`, `variabilityScore`, `falseAlarmProbability` and `falseDiscoveryQValue`, and each variable candidate carries the last six.

### False alarm probability

**What it answers.** The three thresholds say how a star compares with its field, not how likely it is that noise made it look variable. For each star the pipeline therefore also reports `falseAlarmProbability`: the chance that a constant star would show at least its chi-square by noise alone (`false_alarm_probability`). The chance comes from the chi-square distribution (the spread of chi-square values that pure noise produces) with one less degree of freedom than the star has points. (The degrees of freedom are the number of independent residuals; fitting the mean uses up one.)

**Correcting the error bars first.** The star's reduced chi-square is first divided by the field's error scale, the field's median reduced chi-square and never less than 1 (`field_error_scale`), so a field with error bars that are too small is not flagged wholesale.

**Correcting for many stars.** A field holds many stars, so even at a 1 percent false-alarm chance per star, a few constant stars would pass by luck. The probabilities are therefore turned into `falseDiscoveryQValue` with the Benjamini and Hochberg (1995) step-up rule (`benjamini_hochberg`). This rule controls the false discovery rate: the expected share of false alarms among the stars flagged. A candidate needs a q-value below `FALSE_DISCOVERY_LEVEL` (0.01) as well as a variability score above 1.

**Assumptions.** Both numbers assume noise that is independent from point to point. Slow systematics (red noise: errors that drift together over many points) make them too small, and no test measures how much. A tail below 2.2e-308 is reported as that value, never as 0 (`MINIMUM_PROBABILITY`).

**Status.** The probabilities are checked on constant synthetic fields (partly measured; see [PHO-07](../../../documentation/library_design/validation/photometry.md#pho-07)). The 0.01 level is a design value.

### On real data

On a real field the score ranks the catalogued variables well below its synthetic performance, and the rule finds only the stronger variables (partly measured on M 13; see [PHO-08](../../../documentation/library_design/validation/photometry.md#pho-08)). Most catalogued variables there are small-amplitude or sit in the crowded core, so the limit is the detection threshold and not the statistic (see [PHO-09](../../../documentation/library_design/validation/photometry.md#pho-09)). The rule is stricter than its 1 percent level suggests; whether to loosen it is a choice between missed signals and false flags.

The run's summary carries the fitted noise-model curve as `noiseModelCurve`.

### The CV-only flag on the library

The CV-only flag is the fallback rule: flag a star whose CV is above the field's median CV plus 7.4 MAD.

The multiplier was chosen to flag about the top 3 percent of stars. On the library, the CV separates catalogued variables from other stars no better than chance, and the cutoff flags a larger share than that (measured; see [PHO-10](../../../documentation/library_design/validation/photometry.md#pho-10)).

**What it means.** A CV-only flag is not evidence of the kind of variable the catalogs know; the gates `variability_discrimination` and `detectable_amplitude` say so for each run. Raising the multiplier trims the false flags but does not change this: the scatter of the photometry, not the multiplier, is the limit.

### Period searches

Period searches use a family-wise correction: each false-alarm probability is adjusted for every search made on the target (see `processing/family_wise_correction.py`), so trying many stars and many periods does not inflate the chance of a false detection. They measure that probability against shuffled copies of the light curve that keep correlated noise (see `processing/periodicity_search.py`): a point shuffle moves single points at random, which breaks up any slow drift; a block shuffle moves runs of neighbouring points together, which keeps it. The searches use the block shuffle, because a point shuffle calls pure red noise significant almost every time (partly measured on simulated red noise; see [PHO-11](../../../documentation/library_design/validation/photometry.md#pho-11)). How often the searches call white noise a detection, and which injected signals they find, is in [PHO-12](../../../documentation/library_design/validation/photometry.md#pho-12).

## How a period verdict is reached

A cycle or dip search reports a verdict (`detected`, `possible`, `not_detected`, `insufficient_data`). Three things can change it after the search itself:

1. **The family-wise correction.** The false-alarm probability is adjusted for every search made on the target (see above).
2. **The held-out and alias checks** (`processing/period_checks.py`), made on each `detected` or `possible` result. An alias is a false period that the gaps in the observing schedule (for example, observing only at night) can produce next to the real one.
   - For a cycle: each night is left out in turn, the period is found again from the other nights, and the result must predict the left-out night better than the same curve placed at a random phase does (the phase is the position within one cycle, from 0 to 1). More than half the testable nights must agree.
   - For aliases: observing once a night puts echoes of every signal at the best frequency plus and minus the schedule's own repeat frequency (about one cycle a day). An echo whose power is 90 percent or more of the best peak's means the period cannot be told from its alias. 90 percent is a design value.
   - For a dip: the dips are predicted from the other nights and must be seen in the left-out ones.
3. **The rule for combining them.** A failed check lowers the verdict one level (`detected` to `possible`, `possible` to `not_detected`), once per failed check, and the verdict before any lowering is kept in `uncorrectedVerdict`. A check that could not run (fewer than three nights, or no night with enough measurements) lowers nothing and sets `unconfirmed`, so the result reads as "possible, not yet confirmed on held-out data" and not as a clean pass. Each check's own answer is in `verdictChecks`.

The alias check is limited by the nights' length: a night of four hours cannot resolve a frequency difference of one cycle a day, so the check names an alias as a rival only when it is nearly as strong as the best peak.

For exact behavior, read the code.
