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
  stages.
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
