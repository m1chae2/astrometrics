# Spectroscopy pipeline

This document describes what happens when the pipeline turns a spectroscopy
image, or a whole observing session of them, into a classified spectrum for
each star. It describes the stages, not the code. Read the modules
themselves for exact methods and thresholds.

## The three stages

1. **[Pre-processing](pre_processing/README.md)** turns the raw image into a
   calibrated spectrum for each star and measures how good the raw data was.
2. **[Processing](processing/README.md)** finds things in that calibrated
   spectrum: what features it contains, what type of star it resembles, and
   what its own colour is.
3. **[Post-processing](post_processing/README.md)** evaluates the result: it
   checks the result against what the pipeline already knew about the star
   and produces an overall trust score for the classification.

Each star's spectrum moves through all three stages in order. The pipeline
stores the result of each stage on the star, so a reviewer can see the
final classification along with the input quality and output trust score
behind it.

## How this runs in practice

- **One image at a time**: the pipeline matches a single spectral image
  against a normal image of the same field to work out which star is which,
  then extracts every star's spectrum and carries it through all three
  stages. When the target has no normal image of its own, the pipeline names
  only the star at the frame centre: it uses the target's name first, then
  the brightest catalog star near the mount's reported position (see
  `astrometry/processing/README.md`). A planet, the Moon or the Sun gets no
  star name, so its spectrum is not saved to the star catalog.
- **A whole observing session at a time**: the pipeline runs the same three
  stages once per frame in the session, in parallel, using star positions
  it works out from one reference frame in that session.

Either way, the pipeline saves each star's result to the shared star
catalog as soon as its three stages finish, so a crash partway through a
session does not lose the frames already processed.

## Two things that sit outside the three stages

- **Utilities** (`utilities/`) hold standalone tools that are not part of
  processing a target's spectra: deriving a camera's physical calibration
  ahead of time, and checking how well a stack of spectral images aligned.
- The files at this folder's root (`pipeline.py`, `runner.py`, `batch.py`,
  `frame_analysis.py`) drive a star or a session through the three stages
  above. They implement the "how this runs in practice" section, not a
  fourth stage.

## The gate record

Besides each star's own quality records, a run keeps one record per run-level check in the summary's `gates` (built in `post_processing/run_gates.py`, by both `runner.py` and `batch.py`). Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. The tests in `test/post_processing/test_run_gates.py` give every gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `spectra_extracted` | No star produced a spectrum | Never |
| `zero_order_saturation` | 0.1% or more of a zero-order image is saturated | No zero-order saturation was measured |
| `spectral_classification` | A star's type is low-confidence or ambiguous | No star was given a type |
| `catalog_agreement` | A measured type disagrees with the catalog type | No star had a catalog type to compare with |
| `feature_significance` | A star's feature p-values fell back to assuming Gaussian noise | No star had its features tested |
| `resolution_measured` | Never | The resolution was assumed from the instrument design for every spectrum |

`catalog_agreement`, `feature_significance` and `spectra_extracted` are new flags: before, a disagreement with the catalog or an uncalibrated p-value showed only on the star's own record, and a run with no spectra was not flagged at all. The counts behind the gates come from `spectrum_facts`, which the batch workers return per frame so the parallel path builds the same gates as the single-image path. Second-order contamination and the emission-line detector have no run-level gate yet: their limits (2% and 10%, and 5σ on M 57) are not validated, which is Gap 2 of the audit plan.
