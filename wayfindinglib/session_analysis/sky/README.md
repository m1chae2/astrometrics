# Sky-position analysis

This folder asks whether the equipment performed measurably worse in some part of the sky. Low in the sky, light crosses more air. One compass direction may have a building or warm ground. A German equatorial mount tracks from one side of its pier, then flips to the other, and the two sides can behave differently.

Unlike the guiding and capture analyses, this one judges every recorded night together, because a comparison between parts of the sky needs several nights in each part. The request's `session_id` names the span of nights (`first..last`). `pipeline.py` runs the three stages in order and builds the summary (`SkyAnalysis` in `wayfindinglib/models/session/sky_quality.py`).

## Stages

1. `pre_processing/` — can the data answer the question? See its README.
2. `processing/` — how does each part of the sky compare? See its README.
3. `post_processing/` — what should change? See its README.

`sky_bins.py` divides the sky into altitude bands (15 degrees), azimuth sectors (eight, 45 degrees each) and pier sides. Each dimension is analysed on its own.

## What it compares

Three measurements, each with a position:

- **Star width** (arcseconds) and **star roundness** (narrow axis over wide axis), from the imaging frames the science library registered.
- **Guiding error** (arcseconds per axis), from each guiding run of a night whose guiding data was trustworthy.

## Why a night counts only if it saw two parts of the sky

Seeing changes from night to night much more than between parts of the sky, so every measurement is divided by its own night's typical value. A night that stayed in one altitude band then has a relative value of exactly 1 in that band. That says nothing about whether the band is worse than another, so the night does not count toward that dimension. The analysis judges a part of the sky only when at least three nights observed it along with another part.

## What it does not do

It does not separate altitude from time of night. Targets sink as the night goes on, so a poor low band can be a poor hour (seeing, or focus that drifted since the last autofocus). The recommendation says so.

For exact behavior, read the code. The code is the source of truth.
