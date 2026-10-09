# Photometry

This part of the code measures how bright stars are in a series of pictures, and checks whether any of them change brightness over time. That second part is called "variability," and a star that changes brightness in a real way is called a variable star.

## What the pipeline does, step by step

1. **Collect the pictures.** The pipeline looks at every picture taken of a target and groups them into observing sessions. A session is one continuous night (or run) of pictures taken with the same setup. The pipeline leaves out pictures taken through a spectroscopy filter, since measuring a star's total brightness does not make sense on a dispersed spectrum image.
2. **Measure each star's brightness, frame by frame.** For each session, the pipeline uses the first picture to find every star in the field. It then compares every later picture in that session against the first one: it re-locates each star, measures how much light it collected, and records that as one point in the star's brightness history. This step lives in `pre_processing/`.
3. **Compare stars against each other.** A single star's raw brightness bounces around from picture to picture for reasons that have nothing to do with the star itself: clouds, changing air quality, small changes in tracking. To remove that noise, the pipeline picks a group of steady comparison stars in the same field and uses them to correct every star's brightness, frame by frame. It then drops any frame that still looks wrong after that correction. This step lives in `processing/`.
4. **Decide which stars are actually variable.** Once brightness is corrected, the pipeline compares how much each star's brightness varies against how much an ordinary, non-variable star in the same field varies. A star that varies much more than that baseline is flagged as a possible variable star. This also lives in `processing/`.
5. **Look for repeating patterns.** For stars with enough data points, the pipeline also searches for a period: a repeating pattern in brightness over time, such as an eclipsing binary star or a transiting planet. This also lives in `processing/`.
6. **Judge how much to trust the results.** Two separate quality checks run alongside the steps above. One checks how good the raw data behind a star's brightness history was (`pre_processing/`). The other checks how confident the pipeline is in a star's variability result (`post_processing/`).
7. **Combine sessions.** When a target has more than one observing session, the pipeline matches up the same stars between sessions and combines their brightness histories into one longer record, which can reveal slower changes that a single session would miss.

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
| `capture_timestamps` | A frame has no capture time | Never |
| `session_content` | A session produced no light curves | The run had no sessions |
| `session_plate_solve` | A session could not be plate-solved for cross-session matching | There is only one session |
| `photometry_work` | The run found nothing to do (the reason is given) | Never |
| `comparison_ensemble` | A frame was normalized against fewer than ten comparison stars | No frame was normalized |
| `registration_drift` | Frame alignment drifted more than 20 pixels, so tracking was probably lost | No star recorded its drift |
| `scatter_population` | Never | Fewer than ten stars have a measured scatter, so the variable-star cutoff is not reliable |
| `variability_discrimination` | Among the field's stars with a light curve, the scatter of those the catalogs list as variable is not clearly higher than that of the others (AUC not above chance at the 5% level) | Fewer than 10 catalogued variables or 30 unlisted stars in the field |
| `detectable_amplitude` | The run's cutoff means a variable must change by more than about 0.3 mag peak to peak to be flagged | The run has no cutoff |

`registration_drift` and `comparison_ensemble` are new flags: before, a lost-tracking night or a thin comparison ensemble only showed on each star's own record or in the log. The limits for `scatter_population` and the 7.4 multiplier in the variable-star cutoff are not yet backed by a measured false-alarm rate (Gap 2 of the audit plan).

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
