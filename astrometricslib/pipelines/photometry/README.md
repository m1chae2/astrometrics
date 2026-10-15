# Photometry pipeline

The photometry pipeline measures how bright stars are in a series of frames and checks whether any of them change brightness over time. That second part is called *variability*, and a star that changes brightness in a real way is a *variable star*.

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## Terms used here

- **Frame.** One camera exposure file.
- **Session.** In this pipeline, a *session* is the frames of the target taken on one observing night (noon to noon, local time) with the same camera gain setting and offset. The offset is the camera's constant added to every pixel so that noise below zero is still recorded. A change of filter or exposure time does not start a new session (`pipelines/shared/target_sessions.py`).
- **Differential photometry.** *Photometry* is the measurement of a star's brightness. A single star's measured brightness changes from frame to frame for reasons that have nothing to do with the star: thin cloud, haze, small tracking errors. Those changes hit every star in the frame at once. *Differential photometry* divides each star's brightness by the brightness of other stars in the same frame, so the shared changes cancel and the star's own changes remain.
- **The comparison ensemble.** The stars used for that division form the *comparison ensemble* (or comparison set): up to 20 steady, well-measured stars per session. Their combined, weighted brightness is the yardstick for every other star.
- **The light curve.** A *light curve* is one star's brightness listed against time, one point per frame. Each point carries an error bar (its 1σ uncertainty, the size of the random scatter expected from one measurement to the next).
- **Variability indices.** A *variability index* is one number that summarizes how much a light curve wanders, compared with what noise alone would give. The pipeline uses three of them and flags a star only when all three agree.
- **Period search.** A *period search* looks for a pattern that repeats at a fixed interval, such as a pulsating star or the regular dip of an eclipse.
- **Plate-solved.** Matched to a star catalog, so each pixel has a known sky position.
- **Magnitude (mag).** The astronomers' logarithmic brightness scale, `-2.5 log10(flux)`. A larger magnitude is fainter, and a change of 0.01 mag is about 1% in brightness.
- **CV.** The *coefficient of variation*: a light curve's standard deviation divided by its mean.
- **MAD.** The *median absolute deviation*: the median distance of a set of values from their median, a measure of spread that a few wild values cannot inflate. 1.4826 times the MAD estimates the standard deviation for normally distributed data.
- **CV cutoff.** The fallback rule for fields too small for a noise model (fewer than 20 usable stars). A star is flagged when its CV is above the field's median CV plus 7.4 times the MAD of the field's CVs, and never below 0.02 (see [The CV-only flag on the library](#the-cv-only-flag-on-the-library)).
- **Noise model.** In this document, *noise model* always means the measured curve of how much scatter a constant star of each brightness shows in this field. The noise the camera's physics predicts for one measurement is the *predicted noise* (see [Uncertainties](#uncertainties)).
- **Variability score.** One number per star built from the three indices (see [Deciding which stars are variable](processing/README.md#deciding-which-stars-are-variable)). It is above 1 only when all three indices pass their thresholds.
- **Anchors.** The up to 50 bright, unsaturated stars whose positions give each frame's alignment. The main rule picks them by brightness above the sky noise. The fallback rule, used only for sparse fields, takes the stars ranked 50 to 100 by brightness (see [Alignment anchors](pre_processing/README.md#alignment-anchors)).
- **Percentile.** The 95th percentile is the value that 95% of the measurements fall below.
- **ADU.** The *analog-to-digital unit*: one step of the stored pixel value.

## What the pipeline does, in order

**Why it is hard.** Faint stars are noisy, so a rule that simply picks the stars with the most scatter picks mostly faint stars. A field also holds hundreds or thousands of stars, so some constant stars will look variable by chance. The pipeline therefore compares each star with constant stars of the same brightness, and corrects its false-alarm chance for the number of stars tested.

1. **Collect the frames.** The pipeline looks at every frame taken of a target and groups them into sessions. It leaves out frames taken through a spectroscopy filter, since measuring a star's total brightness does not make sense on a dispersed spectrum image (a frame where each star is spread into a spectrum trail).
2. **Measure each star's brightness, frame by frame.** This step lives in `pre_processing/`.
   - *Find the stars.* For each session, the pipeline uses the first frame to find every star in the field.
   - *Line up each frame.* It then compares every later frame in that session against the first one. It moves each star by the frame's alignment: a shift, a rotation and a change of scale, measured on up to 50 bright, unsaturated stars, not on noise peaks. (A saturated star is one so bright that its pixels hit the camera's maximum value, so its true brightness is lost.)
   - *Measure the light.* It re-centers each star on its own brightness-weighted centroid (the average pixel position, weighted by pixel brightness). It measures how much light the star collected in a circle at that exact position (the aperture), and records that as one point in the star's brightness history. Each point also gets its 1σ uncertainty (see [Uncertainties](#uncertainties)).
   - *Time-stamp the point.* The time of that point starts from the frame's `DATE-OBS` header (the capture time written into the image file). The pipeline converts it to the middle of the exposure in BJD_TDB, a time scale that removes the effect of Earth's motion around the Sun (see [Times](#times)). A frame with no readable `DATE-OBS` is rejected, not given the current time.
3. **Compare stars against each other.** A single star's raw brightness bounces around from frame to frame for reasons that have nothing to do with the star itself: clouds, changing air quality, small changes in tracking. To remove that noise, the pipeline picks one fixed group of up to 20 steady comparison stars per session. They are bright but not the very brightest, unsaturated in every frame, not listed as variable by a catalog, and checked for constancy. The pipeline builds a weighted average of their brightness for each frame and divides every star's brightness by it. It then drops any frame that still looks wrong after that correction. It does not fit a star's own brightness against airmass (how much atmosphere the light passed through; 1 straight overhead, larger toward the horizon). This step lives in `processing/` (`comparison_ensemble.py` chooses the group).
4. **Decide which stars are actually variable.** Once brightness is corrected, the pipeline fits the field's noise model. It then measures three indices per star:
   - excess scatter over the model (how many times noisier the star is than a constant star of the same brightness);
   - reduced chi-square against the star's errors (the average squared distance of the points from their mean, in units of their error bars; about 1 for a constant star);
   - the Stetson J index of correlated change (positive when neighboring points tend to move in the same direction, as they do in a real slow change).

   It combines the three into one variability score, which is above 1 only when all three pass their thresholds. It flags a star as a possible variable when its score is above 1 and its q-value is below 0.01. The q-value is the false discovery rate (FDR) at which the star would just be flagged: the expected share of false alarms among all stars flagged at that level (see [False-alarm probability](#false-alarm-probability)). This also lives in `processing/`, which gives the definitions and the thresholds. The score's AUC against catalog labels (the chance that a cataloged variable gets a higher score than an unlisted star; 0.5 is chance, 1 is perfect) is reported under [What the variable-star flag can see](#what-the-variable-star-flag-can-see).
5. **Look for repeating patterns.** A period is a repeating pattern in brightness over time, such as an eclipsing binary star (two stars that pass in front of each other) or a transiting planet (a planet that crosses its star's face). The pipeline searches for a period in only a few stars: the target's own star and the 10 brightest other stars with enough data points (`MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH` in `batch.py`) (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)). Each search takes several seconds, so a field of thousands of stars cannot all be searched; any other star can be searched on request. The searches weight each point by its uncertainty and measure time in BJD_TDB days when the star has them. This also lives in `processing/`.
6. **Judge how much to trust the results.** Two separate quality records run alongside the steps above. One records how good the raw data behind a star's brightness history was (`pre_processing/`). The other records how far each star's CV sits from the field's CV cutoff, and builds the run's gates (`post_processing/`). The [post-processing README](post_processing/README.md#two-rules-and-which-one-the-margin-describes) explains when that margin describes the actual variable-star decision.
7. **Combine sessions.** When a target has more than one session, the pipeline matches up the same stars between sessions and combines their brightness histories into one longer record (partly measured; see [PHO-14](../../../documentation/library_design/validation/photometry.md#pho-14)). The combined record keeps each session's brightness level as measured. It does not rescale one session to match another. The pipeline then measures how much each star's level differs between sessions (see [Change between sessions](#change-between-sessions)), which can reveal slower changes that a single session would miss.

## Uncertainties

Every flux (the light a star delivered, after the sky is subtracted) comes with a 1σ uncertainty (one standard deviation), so a later step can weight a point by how well it was measured.

- `fluxErrors` goes with `fluxes`, in ADU per second.
- `fluxesNormalizedErrors` goes with `fluxesNormalized`. It has no unit. It combines the star's own error with the error of the comparison-star level it was divided by (`1 / sqrt(sum of weights)` for the weighted mean).
- `fluxesDetrendedErrors` goes with `fluxesDetrended`. It has no unit. `fluxesDetrended` equals `fluxesNormalized`, and its errors equal the normalized errors, unless the optional ensemble airmass correction is on (see [Airmass](processing/README.md#airmass)). The pipeline never fits a star's own flux against airmass.

### Where the error comes from

A camera's noise has a few known sources: the random arrival of light (photon or shot noise, which grows as the square root of the count), the electronics that read each pixel (read noise), and the sky glow under the star. The CCD equation (the noise budget of a camera sensor) adds them up. It gives the variance (the error squared) of the flux, in electrons:

`variance = F + n_pix (S + RN^2 + D) + (n_pix^2 / n_sky) (S + RN^2)`

`F` is the star's net counts, `S` the sky counts per pixel, `RN` the read noise, `D` the dark current per pixel (signal the sensor makes by itself from heat), `n_pix` the number of pixels in the circle, and `n_sky` the number of pixels in the sky ring. All of these are in electrons except the pixel counts. The code converts the result back to ADU with the camera's gain (electrons per ADU) and divides by the exposure time, the same way it stores the flux. The code takes the dark current as zero, and the equation assumes the frame has had its bias level (the fixed offset the camera adds) removed.

### Gain and read noise

**Where the numbers come from.** The gain and read noise come from the camera's profile (`gain_e_per_adu` and `read_noise_e` in `models/camera_profile.py`), then from the frame's `EGAIN` and `RDNOISE` header cards. The code never uses the `GAIN` card, because many cameras write a gain setting there and not electrons per ADU.

**When nothing gives them.** The code assumes 1 electron per ADU and no read noise, and the light curve records that in `errorsAssumeUnitGain` and `errorsAssumeZeroReadNoise`. With a wrong gain the errors have the right shape but the wrong size. The `flux_uncertainty` gate reports the median error in magnitudes (`1.0857 x error / flux`; 1.0857 is 2.5 / ln 10, which turns a small fractional error into magnitudes) and whether the gain was assumed.

**The ASI 533MM Pro's numbers.** The camera's profile and the shipped example config hold a gain of 0.783 e-/ADU and a read noise of 3.8 e- (partly measured; see [PHO-03](../../../documentation/library_design/validation/photometry.md#pho-03)). The gain is in electrons per stored ADU. The sensor is 14-bit and the files are 16-bit in steps of 4, so it is a quarter of the datasheet's value in sensor ADU. A gain of 3.6 e-/ADU, a datasheet figure for the sensor's own 14-bit ADU, is wrong for these files and makes the error bars about 2 times too small.

**The 5 times limit.** `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` is set between clear and cloudy real fields (partly measured; see [PHO-02](../../../documentation/library_design/validation/photometry.md#pho-02)). A ratio far above it means patchy cloud or tracking loss, which dividing by the ensemble cannot remove. In clear sessions the predicted errors still run somewhat smaller than the measured scatter, because the CCD equation leaves out flat-field residuals and centroid error (partly measured; see [PHO-02](../../../documentation/library_design/validation/photometry.md#pho-02)).

A light curve stored without errors has empty error lists, and the searches then run without error weights (see [Looking for repeating patterns](processing/README.md#looking-for-repeating-patterns)).

## Times

`timestamps` keeps the moment the shutter opened, in UTC, as written in `DATE-OBS`. `timeBjdTdb` holds, for each timestamp, the middle of the exposure as a Julian Date (a running count of days) in BJD_TDB (Barycentric Julian Date in Barycentric Dynamical Time), in days.

**Why it matters.** Light from a star reaches Earth earlier or later depending on where Earth is in its orbit. BJD_TDB takes the time light would arrive at the center of mass of the solar system (the barycenter), so the date of an event does not depend on where Earth is in its orbit. The shift is up to about 8 min, and a 300 s exposure shifts the time by another 150 s. `pre_processing/observation_times.py` does the conversion with astropy.

The conversion needs two inputs:

- The sky position of the target: the target's own right ascension and declination (the sky's longitude and latitude), or, when the target has none, the reference point of the session's reference-frame plate solution. One position serves the whole field, which keeps the error under a few seconds for a field of about 1°.
- The observatory, from `latitude`, `longitude` and `elevation` in the `[Observatory.Location]` section of the configuration. Without them, the code takes the observer at Earth's center, which changes each time by under 25 ms, and `timeBasis` says so.

`timeBasis` is one of three sentences: `BJD_TDB, mid-exposure`, the same with "observer taken at Earth's center (no site configured)", or `UTC start, no BJD_TDB (target position unknown)`, where `timeBjdTdb` is empty. The `capture_timestamps` gate quotes the run's least exact basis. The period searches use `timeBjdTdb` when a star has one value per timestamp, and the UTC timestamps otherwise.

## Change between sessions

Within a session, a star's normalized flux is its flux divided by a weighted mean of the session's comparison stars, so it has no units (the exact definition is in [The signal and the normalized flux](processing/README.md#the-signal-and-the-normalized-flux)). The pipeline picks the comparison stars separately for each session, and each session keeps the same stars for every frame. Two sessions can therefore use different comparison stars, and a difference in a star's level between them can come from the comparison stars and not from the star.

For that reason, the merge across sessions (`batch.py`, `_merge_light_curves`) does not rescale any flux. It joins the sessions in time order, for both `fluxesNormalized` and `fluxesDetrended`. The airmass detrend runs on one session at a time and keeps the session's mean level, so the joined detrended values keep each session's trend removal and each session's level.

The merge joins `fluxErrors`, `fluxesNormalizedErrors`, `fluxesDetrendedErrors` and `timeBjdTdb` the same way. The merge drops an array that only one session has, because it cannot be paired with the merged timestamps. The merged flags say "assumed" if either session assumed.

The merge also records one `sessionSummaries` entry per session on the light curve.

| Field | Meaning |
| --- | --- |
| `sessionId` | The session the entry describes |
| `pointCount` | Usable (positive) normalized points in the session |
| `medianNormalizedFlux` | The star's median normalized flux in the session (no units) |
| `normalizedFluxScatter` | 1.4826 times the MAD of the star's normalized flux in the session (no units) |
| `comparisonStarCount` | The number of comparison stars per frame (the same for every frame) |
| `ensembleMedianFlux` | The median, over frames, of the comparison signal (ADU per second) |
| `comparisonStarIds` | The ids of the session's comparison stars |
| `comparisonScatterMag` | The scatter of the comparison stars' own normalized light curves, in magnitudes (median over the set of `2.5 log10(1 + CV)`) |
| `comparisonRejectedCount` | The candidates the constancy check dropped plus the stars left out for being listed as variable |

`identify_long_term_variable_candidates` (`processing/variability_analyzer.py`) reads these entries. It asks two questions: how big is the change between sessions, and is it bigger than the noise could explain? For a star with at least two sessions of three or more points, it computes:

- the amplitude, `2.5 log10(highest session median / lowest session median)`, in magnitudes (`betweenSessionAmplitudeMag`);
- the significance, the difference of those two medians divided by their combined expected error (`betweenSessionSignificance`, no units). A significance of 3 means the difference is three times its expected error. The expected error of one median is `1.2533 x normalizedFluxScatter / sqrt(pointCount)`; 1.2533 is sqrt(pi / 2), the factor by which the median of normally distributed values is noisier than their mean.

It flags the star when the significance is above 3 and the amplitude is at least 0.02 mag (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)). This search does not change the within-session scatter (the coefficient of variation).

**How to read a flag.** A flag means that the star's level differs between sessions by more than its own scatter explains. It does not rule out the comparison stars as the cause. Compare the amplitude with the amplitudes of the other stars in the field, and with the `comparisonStarCount`, `comparisonStarIds`, `comparisonScatterMag` and `ensembleMedianFlux` of each session, before treating a flag as a variable star.

## Known variables

Each variable candidate carries `knownVariability` and a one-sentence `knownVariabilityNote`, filled in from the saved catalog row by `post_processing/known_variability_labels.py`. They say whether SIMBAD, Gaia DR3 or VSX (whichever have been asked) already list the star as variable. (SIMBAD is a general star database, Gaia DR3 is the third data release of the Gaia satellite survey, and VSX is the International Variable Star Index.) A candidate the catalogs list is not a discovery. `unknown` means no catalog was asked about the star, which is not the same as `not_listed_as_variable`; the note names the catalogs that were consulted and the ones that were not.

## What the variable-star flag can see

The flag picks candidates with the noise model and the three indices described in [Deciding which stars are variable](processing/README.md#deciding-which-stars-are-variable). The score is checked on synthetic fields with known variables (partly measured; see [PHO-06](../../../documentation/library_design/validation/photometry.md#pho-06)) and on one real field (partly measured; see [PHO-08](../../../documentation/library_design/validation/photometry.md#pho-08)). The `variability_discrimination` gate fails a run whose AUC against catalog labels is below 0.7. No AUC against catalog labels for the score on the whole library is measured.

Each light curve carries the indices as `instrumentalMag`, `rmsMag`, `excessScatter`, `reducedChiSquare`, `stetsonJ`, `variabilityScore`, `falseAlarmProbability` and `falseDiscoveryQValue`, and each variable candidate carries the last six.

### False-alarm probability

**What it answers.** The three thresholds say how a star compares with its field, not how likely it is that noise made it look variable. For each star the pipeline therefore also reports `falseAlarmProbability`, the false-alarm probability (FAP): the p-value of the chi-square test, the chance that a constant star would show at least its chi-square by noise alone (`false_alarm_probability`). The chance comes from the chi-square distribution (the spread of chi-square values that pure noise produces) with one less degree of freedom than the star has points. (The degrees of freedom are the number of independent residuals; fitting the mean uses up one.)

**Correcting the error bars first.** The pipeline first divides the star's reduced chi-square by the field's error scale, the field's median reduced chi-square and never less than 1 (`field_error_scale`), so a field with error bars that are too small is not flagged wholesale.

**Correcting for many stars.** A field holds many stars, so even at a 1% false-alarm chance per star, a few constant stars would pass by luck. The pipeline therefore turns the probabilities into `falseDiscoveryQValue` with the Benjamini and Hochberg (1995) step-up rule (`benjamini_hochberg`). This rule controls the false discovery rate (FDR): the expected share of false positives among the stars flagged. A candidate needs a q-value below `FALSE_DISCOVERY_LEVEL` (0.01) as well as a variability score above 1.

**Assumptions.** Both numbers assume noise that is independent from point to point. Slow systematics (red noise: errors that drift together over many points) make them too small, and no test measures how much. A tail below 2.2e-308 is reported as that value, never as 0 (`MINIMUM_PROBABILITY`).

**Status.** The probabilities are checked on constant synthetic fields (partly measured; see [PHO-07](../../../documentation/library_design/validation/photometry.md#pho-07)). The 0.01 level is a design estimate (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)).

### On real data

On a real field the score ranks the cataloged variables well below its synthetic performance, and the rule finds only the stronger variables (partly measured; see [PHO-08](../../../documentation/library_design/validation/photometry.md#pho-08)). Most cataloged variables there are small-amplitude or sit in the crowded core, so the limit is the detection threshold and not the statistic (partly measured; see [PHO-09](../../../documentation/library_design/validation/photometry.md#pho-09)). The rule is stricter than its 1% level suggests; whether to loosen it is a choice between missed signals and false flags.

The run's summary carries the fitted noise-model curve as `noiseModelCurve`.

### The CV-only flag on the library

The CV-only flag is the fallback rule: flag a star whose CV is above the field's median CV plus 7.4 MAD.

The multiplier was chosen to flag about the top 3% of stars. On the library, the CV separates cataloged variables from other stars no better than chance (an AUC against catalog labels near 0.5), and the cutoff flags a larger share than 3% (measured; see [PHO-10](../../../documentation/library_design/validation/photometry.md#pho-10)).

**What it means.** A CV-only flag is not evidence of the kind of variable the catalogs know; the gates `variability_discrimination` and `detectable_amplitude` say so for each run. Raising the multiplier trims the false flags but does not change this: the scatter of the photometry, not the multiplier, is the limit.

### Period searches

Period searches use a family-wise correction: each false-alarm probability is adjusted for every search made on the target (see `processing/family_wise_correction.py`), so trying many stars and many periods does not inflate the chance of a false detection. They measure that probability against shuffled copies of the light curve that keep correlated noise (see `processing/periodicity_search.py`). A point shuffle moves single points at random, which breaks up any slow drift; a block shuffle moves runs of neighboring points together, which keeps it. The searches use the block shuffle, because a point shuffle calls pure red noise significant almost every time (partly measured; see [PHO-11](../../../documentation/library_design/validation/photometry.md#pho-11)). How often the searches call white noise a detection, and which injected signals they find, is in the register (partly measured; see [PHO-12](../../../documentation/library_design/validation/photometry.md#pho-12)).

## How a period verdict is reached

A cycle or dip search reports a verdict (`detected`, `possible`, `not_detected`, `insufficient_data`). Three things can change it after the search itself:

1. **The family-wise correction.** The false-alarm probability is adjusted for every search made on the target (see [Period searches](#period-searches)).
2. **The held-out and alias checks** (`processing/period_checks.py`), made on each `detected` or `possible` result. An alias is a false period that the gaps in the observing schedule (for example, observing only at night) can produce next to the real one.
   - For a cycle: the check leaves out each night in turn, finds the period again from the other nights, and requires the result to predict the left-out night better than the same curve placed at a random phase does (the phase is the position within one cycle, from 0 to 1). More than half the testable nights must agree.
   - For aliases: observing once a night puts echoes of every signal at the best frequency plus and minus the schedule's own repeat frequency (about one cycle a day). An echo whose power is 90% or more of the best peak's means the period cannot be told from its alias. The 90% is a design estimate (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)).
   - For a dip: the check predicts the dips from the other nights and requires them to be seen in the left-out ones.
3. **The rule for combining them.** A failed check lowers the verdict one level (`detected` to `possible`, `possible` to `not_detected`), once per failed check, and the verdict before any lowering is kept in `uncorrectedVerdict`. A check that could not run (fewer than three nights, or no night with enough measurements) lowers nothing and sets `unconfirmed`. The result then reads as "possible, not yet confirmed on held-out data" and not as a clean pass. Each check's own answer is in `verdictChecks`.

## Settings

- **Comparison stars.** The selection is checked only on synthetic sessions with known truth (partly measured; see [PHO-01](../../../documentation/library_design/validation/photometry.md#pho-01)). No real field has checked the following settings (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)):
  - the brightness band for comparison candidates (ranked by brightness, the brightest 2% are skipped and the band reaches down to the brightest 30%);
  - the minimum of 5 and the cap of 20 comparison stars;
  - the 3σ consistency limit;
  - the rule that a usable frame has at least half the typical number of measured stars.
- **Where they are set.** The cap and the optional ensemble airmass correction are arguments of `VariabilityAnalyzer`; no configuration file sets them.
- **Camera numbers.** The gain and read noise come from the camera profile or the frame header (see [Gain and read noise](#gain-and-read-noise)).
- **Observatory location.** `latitude`, `longitude` and `elevation` in `[Observatory.Location]` set the observer for BJD_TDB (see [Times](#times)).
- **Other limits.** The stage READMEs give each stage's limits with their status: [pre-processing](pre_processing/README.md#settings), [processing](processing/README.md#settings) and [post-processing](post_processing/README.md#settings).

## Quality outputs

A *gate* is a yes-or-no entry on a whole run, as opposed to the quality records kept for each star. Gates catch problems that affect every star at once, such as lost tracking or a poor comparison set.

Besides each star's own quality records, a run keeps one record per run-level gate in the summary's `gates` (built in `post_processing/run_gates.py`). Each gate is `passed`, `failed` or `not_checked`; a gate that could not look is never recorded as passed. The tests in `test/post_processing/test_run_gates.py` give every gate input that must fail it.

| Gate | Fails when | Not checked when |
| --- | --- | --- |
| `ensemble_frame_rejection` | At least five frames were rejected as outliers and they are at least a quarter of all frames | Fewer than five frames |
| `capture_timestamps` | A frame has no usable capture time: none on record, or its `DATE-OBS` header is missing or unreadable (the detail lists the first three reasons) | Never. The detail also names the time scale of the light curves (see [Times](#times)) |
| `session_content` | A session produced no light curves | The run had no sessions |
| `session_plate_solve` | A session could not be plate-solved for cross-session matching | There is only one session |
| `photometry_work` | The run found nothing to do (the reason is given) | Never |
| `comparison_ensemble` | A session's comparison set has fewer than five stars, or the number of comparison stars differs from one frame of a session to another (the set must be fixed), or the comparison stars' own light curves scatter more than 5 times what their errors predict (`MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED`; the measured value is then the ratio). The detail on a pass gives the set sizes, says each set is the same in every frame, and quotes the ensemble scatter in magnitudes next to the scatter the errors predict | No session was normalized |
| `registration_drift` | Any of three limits is passed: lost tracking, a large rotation or scale change, or too many refused centroids (see [The registration drift gate](#the-registration-drift-gate)) | No star recorded its drift and no session has a per-star summary |
| `scatter_population` | Never | Fewer than 10 stars have a measured scatter, or fewer than 20 can be used to fit the noise model, so the CV cutoff stands in for the noise model. A pass quotes the number of stars the model was fitted from |
| `variability_discrimination` | Among the field's stars with a light curve, the variability score of those the catalogs list as variable does not rank above that of the others with an AUC against catalog labels of at least 0.7 (`MINIMUM_DISCRIMINATION_AUC`), also checked against the chance level at the 5% level | Fewer than 10 cataloged variables or 30 unlisted stars in the field |
| `detectable_amplitude` | The gate estimates the smallest variable this run could flag, and fails when that estimate is above 0.3 mag peak to peak (`MAXIMUM_DETECTABLE_AMPLITUDE_MAG`). The estimate is for a sinusoid on a star of median brightness. With a noise model, it is the model's scatter there times `2 sqrt(2) sqrt(1.5^2 - 1)`. Without one, it is the sinusoid whose scatter equals the CV cutoff (see the note below the table) | The run has no noise model and no cutoff |
| `flux_uncertainty` | Never. It passes with the median per-point error in magnitudes as its measured value | No light curve carries errors, or the camera's gain is unknown and the errors assume 1 electron per ADU (the detail still quotes the median error) |

`registration_drift` and `comparison_ensemble` report a lost-tracking session and a thin or unstable comparison set at the level of the run.

**Gate limits.** The 10-star limit for `scatter_population`, the 0.7 floor of `variability_discrimination` and the 0.3 mag limit of `detectable_amplitude` are design estimates (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)). The 7.4 multiplier in the CV cutoff has no measured false-alarm rate, but its behavior on the library is measured (measured; see [PHO-10](../../../documentation/library_design/validation/photometry.md#pho-10)).

**The `detectable_amplitude` estimate.** A sinusoid of half-height `a` adds a scatter of `a / sqrt(2)` (its root-mean-square value), and independent scatters add in quadrature (as the square root of the sum of squares). The candidate rule needs a star's total scatter to reach 1.5 times the noise model's scatter `s`, so `a / sqrt(2) = s sqrt(1.5^2 - 1)`, and the peak-to-peak size `2a` is `2 sqrt(2) sqrt(1.5^2 - 1) s`. A real star also has to pass the other two indices, so it needs slightly more.

**Two different numbers.** The 0.3 mag limit is a design estimate, not a measurement. Pulsating, rotating and young variable stars mostly change by less than about 0.3 mag, while eclipsing binaries, Cepheids, RR Lyrae stars and Miras change by more, so a run that cannot see 0.3 mag is blind to most variables. It is a pass-or-fail line for the run's computed estimate. The size of signal the rule actually finds on real light curves is a separate number (partly measured; see [PHO-09](../../../documentation/library_design/validation/photometry.md#pho-09)).

### The registration drift gate

Registration is the lining up of each frame with the first one. This gate checks that the lining up worked well enough for the apertures to sit on the stars.

**What it checks.** The gate fails when any of three limits is passed:

1. Frame alignment drifted more than 20 pixels, so tracking was probably lost.
2. The 95th percentile, over the frames, of how far the fitted rotation and scale change move a corner exceeds 1 pixel (`MAXIMUM_CORNER_DISPLACEMENT_PX`). The frames are lined up with that rotation, so the apertures follow the stars, but a star lands on different pixels in each frame and the flat-field error adds scatter. (A flat field is the correction for the uneven sensitivity of the sensor's pixels; its small leftover error differs from pixel to pixel.)
3. More than 20% of the star measurements kept the aligned position because the star's own centroid was refused (`MAXIMUM_CENTROID_FALLBACK_FRACTION`).

**What it reports.** The detail reports the global drift, the corner displacement, the median and 95th-percentile per-star offset, the fallback share, the worst frame, and, when a session fell back to the fallback anchor rule, that fact. The per-star offset is reported, not judged, because centroid noise on faint and crowded stars dominates it. With several sessions, the gate reports the worst one. The measured value is the global drift for limit 1, the corner displacement for limit 2 and the fallback share (0 to 1) for limit 3.

**Status.** The three limits are design estimates, not set from a measured false-positive rate (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)). The gate's values on real fields are in the register (partly measured; see [PHO-04](../../../documentation/library_design/validation/photometry.md#pho-04)).

### Per-star metrics

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `excessScatter` | none | 1.5 (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: more scatter than a constant star of the same brightness | `variabilityScore` |
| `reducedChiSquare` | none | Field median plus 2.326 robust standard deviations, in `log10` (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: points sit farther from their mean than their error bars allow | `variabilityScore`, `falseAlarmProbability` |
| `stetsonJ` | none | Field median plus 2.326 robust standard deviations (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)) | Positive: neighboring points move together, as in a slow change | `variabilityScore` (as a veto) |
| `variabilityScore` | none | Above 1 when all three indices pass | High: a stronger candidate | Candidate rule; `variability_discrimination` gate |
| `falseAlarmProbability` | none (probability) | None on its own | Low: noise alone is unlikely to give this chi-square | `falseDiscoveryQValue` |
| `falseDiscoveryQValue` | none | Below 0.01 (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)) | Low: the star stays flagged at a strict FDR | Candidate rule |
| `coefficientOfVariation` | none | CV cutoff: field median plus 7.4 MAD, at least 0.02 (measured; see [PHO-10](../../../documentation/library_design/validation/photometry.md#pho-10)) | High: more scatter relative to the mean | CV-only fallback rule; post-processing margin |
| `betweenSessionAmplitudeMag` | mag | At least 0.02 (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: a larger level change between sessions | `identify_long_term_variable_candidates` |
| `betweenSessionSignificance` | none | Above 3 (design estimate; see [photometry design estimates](../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: the level change is large next to its expected error | `identify_long_term_variable_candidates` |

## Where each piece lives

- [`pre_processing/`](pre_processing/README.md) turns a raw frame into one brightness measurement per star, and judges how good that raw measurement was.
- [`processing/`](processing/README.md) compares stars against each other, decides which ones are variable, and searches for repeating patterns.
- [`post_processing/`](post_processing/README.md) judges how much to trust a variability result and builds the run's gates.
- `runner.py` and `batch.py` run the steps above in order, for one target, and combine the results.
- The [photometry review guide](../../../documentation/library_design/pipelines/photometry.md) summarizes the pipeline for reviewers.

## Limits

- **Between-session changes.** No run gate checks the between-session amplitude. The `detectable_amplitude` gate describes only the within-session cutoff, so a run can pass it and still be unable to see a small change between sessions.
- **Red noise.** The false-alarm probability and the q-value assume noise that is independent from point to point. Slow systematics make them too small, and no test measures how much.
- **The CV-only flag.** On the library the CV separates cataloged variables from other stars no better than chance (measured; see [PHO-10](../../../documentation/library_design/validation/photometry.md#pho-10)).
- **Real fields.** The thresholds are checked on synthetic fields and one real field only (partly measured; see [PHO-08](../../../documentation/library_design/validation/photometry.md#pho-08)).
- **Alias check.** The alias check is limited by the nights' length. A night of 4 h cannot resolve a frequency difference of one cycle a day, so the check names an alias as a rival only when it is nearly as strong as the best peak.

## Scope

The pipeline measures differential brightness of stars in the imaging frames of one target, finds variable-star candidates, and searches a few stars for periods. It leaves out frames taken through a spectroscopy filter and does not fit any star's own brightness against airmass.

For exact behavior, read the code.
