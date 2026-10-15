# Spectroscopy utilities

This folder holds standalone spectroscopy tools used outside the per-run pipeline. Nothing here runs as part of processing a target's spectra.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `calibration_tuner.py` — derives the camera's physical settings from a bright reference star such as Vega and saves them for later runs. It finds the star's known hydrogen absorption lines and uses their positions to calculate the grating-to-sensor distance and where the spectrum starts.
- `registration_quality.py` — checks the alignment quality of a spectroscopy stack after the fact. A spectrum frame has no sharp star to measure, so the check follows the zero-order star (the undispersed image of the star) and tests whether it jumps around or fades out.

For exact behavior, read the code.
