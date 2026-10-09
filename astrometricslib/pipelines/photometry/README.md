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

`registration_drift` and `comparison_ensemble` are new flags: before, a lost-tracking night or a thin comparison ensemble only showed on each star's own record or in the log. The limits for `scatter_population` and the 7.4 multiplier in the variable-star cutoff are not yet backed by a measured false-alarm rate (Gap 2 of the audit plan).

## Known variables

Each variable candidate carries `knownVariability` and a one-sentence `knownVariabilityNote`, filled in from the saved catalog row by `post_processing/known_variability_labels.py`: whether SIMBAD, Gaia DR3 or VSX (whichever have been asked) already list the star as variable. A candidate the catalogs list is not a discovery. `unknown` means no catalog was asked about the star, which is not the same as `not_listed_as_variable`; the note names the catalogs that were consulted and the ones that were not.
