# Photometry

This part of the code measures how bright stars are in a series of pictures, and checks whether any of them change brightness over time. That second part is called "variability," and a star that changes brightness in a real way is called a variable star.

## What the pipeline does, step by step

1. **Collect the pictures.** The pipeline looks at every picture taken of a target and groups them into observing sessions. A session is one continuous night (or run) of pictures taken with the same setup. The pipeline leaves out pictures taken through a spectroscopy filter, since measuring a star's total brightness does not make sense on a dispersed spectrum image.
2. **Measure each star's brightness, frame by frame.** For each session, the pipeline uses the first picture to find every star in the field. It then compares every later picture in that session against the first one: it shifts each star by the picture's overall drift, re-centers it on its own brightness-weighted centroid, measures how much light it collected in a circle at that exact position, and records that as one point in the star's brightness history. Each point also gets its 1-sigma uncertainty (see "Uncertainties" below). The time of that point starts from the picture's `DATE-OBS` header and is converted to the middle of the exposure in BJD_TDB (see "Times" below). A picture with no readable `DATE-OBS` is rejected, not given the current time. This step lives in `pre_processing/`.
3. **Compare stars against each other.** A single star's raw brightness bounces around from picture to picture for reasons that have nothing to do with the star itself: clouds, changing air quality, small changes in tracking. To remove that noise, the pipeline picks one fixed group of up to 20 steady comparison stars per session (bright but not the very brightest, unsaturated in every picture, not listed as variable by a catalog, and checked for constancy). It builds a weighted average of their brightness for each picture and divides every star's brightness by it. It then drops any frame that still looks wrong after that correction. It does not fit a star's own brightness against airmass. This step lives in `processing/` (`comparison_ensemble.py` chooses the group).
4. **Decide which stars are actually variable.** Once brightness is corrected, the pipeline compares how much each star's brightness varies against how much an ordinary, non-variable star in the same field varies. A star that varies much more than that baseline is flagged as a possible variable star. This also lives in `processing/`.
5. **Look for repeating patterns.** For stars with enough data points, the pipeline also searches for a period: a repeating pattern in brightness over time, such as an eclipsing binary star or a transiting planet. The searches weight each point by its uncertainty and measure time in BJD_TDB days when the star has them. This also lives in `processing/`.
6. **Judge how much to trust the results.** Two separate quality checks run alongside the steps above. One checks how good the raw data behind a star's brightness history was (`pre_processing/`). The other checks how confident the pipeline is in a star's variability result (`post_processing/`).
7. **Combine sessions.** When a target has more than one observing session, the pipeline matches up the same stars between sessions and combines their brightness histories into one longer record. The combined record keeps each session's brightness level as measured. It does not rescale one session to match another. The pipeline then measures how much each star's level differs between sessions (see "Change between sessions" below), which can reveal slower changes that a single session would miss.

## Where to look

- `pre_processing/` — turning a raw picture into one brightness measurement per star, and judging how good that raw measurement was.
- `processing/` — comparing stars against each other, deciding which ones are variable, and searching for repeating patterns.
- `post_processing/` — judging how much to trust a variability result.
- `runner.py` and `batch.py` — the code that runs the steps above in order, for one target, and combines the results.

See each subfolder's own README for more detail on that step.

## The gate record

Besides each star's own quality records, a run keeps one record per run-level check in the summary's `gates` (built in `post_processing/run_gates.py`). Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. The tests in `test/post_processing/test_run_gates.py` give every gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `ensemble_frame_rejection` | Five or more frames, and a quarter of all frames, were rejected as outliers | Fewer than five frames |
| `capture_timestamps` | A frame has no usable capture time: none on record, or its `DATE-OBS` header is missing or unreadable (the detail lists the first three reasons) | Never. The detail also names the time scale of the light curves (see "Times") |
| `session_content` | A session produced no light curves | The run had no sessions |
| `session_plate_solve` | A session could not be plate-solved for cross-session matching | There is only one session |
| `photometry_work` | The run found nothing to do (the reason is given) | Never |
| `comparison_ensemble` | A session's comparison set has fewer than five stars, or the number of comparison stars differs from one frame of a session to another (the set must be fixed). The detail on a pass gives the set sizes, says each set is the same in every frame, and quotes the ensemble scatter in magnitudes next to the scatter the errors predict | No session was normalized |
| `registration_drift` | Frame alignment drifted more than 20 pixels, so tracking was probably lost | No star recorded its drift |
| `scatter_population` | Never | Fewer than ten stars have a measured scatter, so the variable-star cutoff is not reliable |
| `variability_discrimination` | Among the field's stars with a light curve, the scatter of those the catalogs list as variable is not clearly higher than that of the others (AUC not above chance at the 5% level) | Fewer than 10 catalogued variables or 30 unlisted stars in the field |
| `detectable_amplitude` | The run's cutoff means a variable must change by more than about 0.3 mag peak to peak to be flagged | The run has no cutoff |
| `flux_uncertainty` | Never. It passes with the median per-point error in magnitudes as its measured value | No light curve carries errors, or the camera's gain is unknown and the errors assume 1 electron per ADU (the detail still quotes the median error) |

`registration_drift` and `comparison_ensemble` report a lost-tracking night and a thin or unstable comparison set at the level of the run. The limits for `scatter_population` and the 7.4 multiplier in the variable-star cutoff are not yet backed by a measured false-alarm rate (Gap 2 of the audit plan).

## Uncertainties

Every flux comes with a 1-sigma uncertainty (one standard deviation), so a later step can weight a point by how well it was measured.

- `fluxErrors` goes with `fluxes`, in ADU per second (ADU is analog-to-digital unit, one step of the stored pixel value).
- `fluxesNormalizedErrors` goes with `fluxesNormalized`. It has no unit. It combines the star's own error with the error of the comparison-star level it was divided by (`1 / sqrt(sum of weights)` for the weighted mean).
- `fluxesDetrendedErrors` goes with `fluxesDetrended`. It has no unit. `fluxesDetrended` equals `fluxesNormalized`, and its errors equal the normalized errors, unless the optional ensemble airmass correction is on (see `processing/README.md`). The pipeline never fits a star's own flux against airmass.

The CCD equation (the noise budget of a camera sensor) gives the flux error, in electrons:

`variance = F + n_pix (S + RN^2 + D) + (n_pix^2 / n_sky) (S + RN^2)`

`F` is the star's net counts, `S` the sky counts per pixel, `RN` the read noise, `D` the dark current per pixel, `n_pix` the number of pixels in the circle, and `n_sky` the number of pixels in the sky ring. All of these are in electrons except the pixel counts. The code converts the result back to ADU with the camera's gain (electrons per ADU) and divides by the exposure time, the same way it stores the flux. Dark current is taken as zero, and the equation assumes the picture has had its bias level (the fixed offset the camera adds) removed.

The gain and read noise come from the camera's profile (`gain_e_per_adu` and `read_noise_e` in `models/camera_profile.py`), then from the picture's `EGAIN` and `RDNOISE` header cards. The `GAIN` card is never used, because many cameras write a gain setting there and not electrons per ADU. When nothing gives the gain, the code assumes 1 electron per ADU and no read noise, and the light curve records that in `errorsAssumeUnitGain` and `errorsAssumeZeroReadNoise`. With a wrong gain the errors have the right shape but the wrong size. The `flux_uncertainty` gate reports the median error in magnitudes (`1.0857 x error / flux`) and whether the gain was assumed.

A light curve saved before the errors existed has empty error lists. The searches then work as they did before.

## Times

`timestamps` keeps the moment the shutter opened, in UTC, as written in `DATE-OBS`. `timeBjdTdb` holds, for each timestamp, the middle of the exposure as a Julian Date in BJD_TDB (Barycentric Julian Date in Barycentric Dynamical Time), in days. BJD_TDB takes the time light would arrive at the center of mass of the solar system, so the date of an event does not depend on where Earth is in its orbit. The shift is up to about 8 minutes, and a 300 s exposure shifts the time by another 150 s. `pre_processing/observation_times.py` does the conversion with astropy.

The conversion needs two inputs:

- The sky position of the target: the target's own right ascension and declination, or, when the target has none, the reference point of the session's reference-frame plate solution. One position serves the whole field, which keeps the error under a few seconds for a field of about one degree.
- The observatory, from `latitude`, `longitude` and `elevation` in the `[Observatory.Location]` section of the configuration. Without them, the code takes the observer at Earth's center, which changes each time by under 25 ms, and `timeBasis` says so.

`timeBasis` is one of three sentences: `BJD_TDB, mid-exposure`, the same with "observer taken at Earth's center (no site configured)", or `UTC start, no BJD_TDB (target position unknown)`, where `timeBjdTdb` is empty. The `capture_timestamps` gate quotes the run's least exact basis. The period searches use `timeBjdTdb` when a star has one value per timestamp, and the UTC timestamps otherwise.

## Change between sessions

Within a session, a star's normalized flux is its flux divided by a weighted mean of the session's comparison stars, so it has no units (the exact definition is in `processing/README.md`). The pipeline picks the comparison stars separately for each session, and each session keeps the same stars for every picture. Two sessions can therefore use different comparison stars, and a difference in a star's level between them can come from the comparison stars and not from the star.

For that reason, the merge across sessions (`batch.py`, `_merge_light_curves`) does not rescale any flux. It joins the sessions in time order, for both `fluxesNormalized` and `fluxesDetrended`. The airmass detrend runs on one session at a time and keeps the session's mean level, so the joined detrended values keep each session's trend removal and each session's level.

The merge joins `fluxErrors`, `fluxesNormalizedErrors`, `fluxesDetrendedErrors` and `timeBjdTdb` the same way. An array that only one session has is dropped, because it cannot be paired with the merged timestamps. The merged flags say "assumed" if either session assumed.

The merge also records one `sessionSummaries` entry per session on the light curve:

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

`identify_long_term_variable_candidates` (`processing/variability_analyzer.py`) reads these entries. For a star with at least two sessions of three or more points, it computes:

- the amplitude, `2.5 log10(highest session median / lowest session median)`, in magnitudes (`betweenSessionAmplitudeMag`);
- the significance, the difference of those two medians divided by their combined expected error (`betweenSessionSignificance`, no units). The expected error of one median is `1.2533 x normalizedFluxScatter / sqrt(pointCount)`.

It flags the star when the significance is above 3 and the amplitude is at least 0.02 mag. The within-session scatter (the coefficient of variation) is not changed by this search. A flag means that the star's level differs between sessions by more than its own scatter explains. It does not rule out the comparison stars as the cause. Compare the amplitude with the amplitudes of the other stars in the field, and with the `comparisonStarCount`, `comparisonStarIds`, `comparisonScatterMag` and `ensembleMedianFlux` of each session, before treating a flag as a variable star.

No run gate checks this amplitude yet. The `detectable_amplitude` gate below describes only the within-session cutoff, so a run can pass it and still be unable to see a small change between sessions.

## Comparison stars: measured and designed

Measured (synthetic sessions with known truth, `test/test_comparison_ensemble_injection.py`): the pipeline recovers a 1 percent, 2 hour dip to within 3 percent of its depth, rejects a comparison candidate that carries a 5 percent sinusoid, matches the comparison scatter to the propagated error within 20 percent, and removes a 3 percent frame-to-frame transparency change from every curve.

Designed (no real field has checked them): the brightness band of 2 to 30 percent, the minimum of 5 and the cap of 20 comparison stars, the 3-sigma consistency limit, and the rule that a usable frame has at least half the typical number of measured stars. The cap and the optional ensemble airmass correction are arguments of `VariabilityAnalyzer`; no configuration file sets them.

## Known variables

Each variable candidate carries `knownVariability` and a one-sentence `knownVariabilityNote`, filled in from the saved catalog row by `post_processing/known_variability_labels.py`: whether SIMBAD, Gaia DR3 or VSX (whichever have been asked) already list the star as variable. A candidate the catalogs list is not a discovery. `unknown` means no catalog was asked about the star, which is not the same as `not_listed_as_variable`; the note names the catalogs that were consulted and the ones that were not.

## What the variable-star flag can see

Measured on the library on 2026-10-09 (`scripts/measure_variability_cutoff.py`, 46,259 stars with light curves in 32 targets, 2,374 of them listed as variable by SIMBAD, Gaia DR3 or VSX): at the pipeline's multiplier of 7.4, the cutoff flags 5.2% of stars, but only 3.8% of the catalogued variables, against 5.2% of the stars no catalog lists. The scatter statistic separates the two no better than chance (AUC 0.46 between stars of similar brightness; 0.5 is chance), including for strong-amplitude types, and the median scatter of a light curve is 8% even among the brightest fifth of stars. The recorded median scatter per target ranges from 0.009 to 0.9 mag (median 0.29). So a flag in most of these runs is not evidence of the kind of variable the catalogs know; the gates `variability_discrimination` and `detectable_amplitude` say so for each run. Raising the multiplier trims the false flags but does not change this: the scatter of the photometry, not the multiplier, is the limit. The "top 3%" the multiplier was chosen for was never measured; it flags 5.2%.

Period searches are judged against every search made on the target (see `processing/family_wise_correction.py`) with a noise model that keeps correlated noise (see `processing/periodicity_search.py`): on simulated red noise with nothing real in it, the old point shuffle called 97% to 100% of light curves significant at the 5% level, the block shuffle about 5% to 10%.

## How a period verdict is reached

A cycle or dip search reports a verdict (`detected`, `possible`, `not_detected`, `insufficient_data`). Three things can change it after the search itself:

1. **The family correction.** The false-alarm probability is judged against every search made on the target (see above).
2. **The held-out and alias checks** (`processing/period_checks.py`), made on each `detected` or `possible` result. For a cycle: each night is left out in turn, the period is found again from the other nights, and the result must predict the left-out night better than a random phase does (more than half the testable nights must agree); and the period is tested against aliases of the observing schedule (a rival peak at 90% or more of the best one at the schedule's own frequency offset means the period cannot be told from its alias). For a dip: the dips are predicted from the other nights and must be seen in the left-out ones.
3. **The rule for combining them.** A failed check lowers the verdict one level (`detected` to `possible`, `possible` to `not_detected`), once per failed check, and the verdict before any lowering is kept in `uncorrectedVerdict`. A check that could not run (fewer than three nights, or no night with enough measurements) lowers nothing and sets `unconfirmed`, so the result reads as "possible, not yet confirmed on held-out data" and not as a clean pass. Each check's own answer is in `verdictChecks`.

The alias check is limited by the nights' length: a night of four hours cannot resolve a frequency difference of one cycle a day, so the check names an alias as a rival only when it is nearly as strong as the best peak.
