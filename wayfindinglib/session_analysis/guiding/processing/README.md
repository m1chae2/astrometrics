# Processing: what does the guiding data show?

`measure_guiding_performance.py` turns the night's guide samples into the numbers that describe how well the mount tracked. The result is a `GuidingPerformance`.

## What it measures

- **Guiding error** (`rmsRaArcsec`, `rmsDecArcsec`, `rmsPerAxisArcsec`, in arcseconds). How far the guide star wandered from its target, along right ascension (RA) and declination (Dec), and the two combined per axis. It uses a robust spread (a median absolute deviation) so a few wild samples cannot distort it, and it leaves out the excursions that pre-processing counted. A lower value is better. `rmsPerAxisArcsec` is the number compared with the acceptable guiding error.
- **Error including excursions** (`rmsIncludingExcursionsArcsec`). The ordinary root-mean-square of every sample. The gap between it and the guiding error shows how much the excursions matter.
- **Expected star widening** (`expectedStarWideningFraction`, a fraction). How much this guiding error widens a star, given the star width the equipment's own frames show. It is `sqrt(1 + (2.3548 x error / star width)^2) - 1`. A value of 0.1 means stars are about 10 percent wider than they would be with perfect guiding. It is unknown if the star width is unknown.
- **Net Dec correction rate** (`netDecCorrectionArcsecPerMinute`, arcseconds per minute, positive is north). How far, on average, the guider moved the mount along Dec. It is the total of the Dec corrections times the calibrated Dec speed, divided by the run's length. Only runs of at least ten minutes with a possible calibrated speed are used. It describes what the guider did and nothing more; it is not a polar alignment estimate (see the guiding README).
- **Exposure-length view** (`exposureFeasibility`, `longestReliableExposureSeconds`). For each exposure length the equipment is used with (the lengths with at least 10 light frames that are long enough for guiding to matter), how often guiding stayed clean for a whole exposure. The analysis tries every start time during the night's guiding, every quarter of the exposure length, and keeps the start times where the exposure would fit inside one guiding run. An exposure is clean when the star is never lost (no gap of more than four guide cycles between samples), nothing jumps (no sample beyond the excursion limit), and the error scatter about its own mean stays under the acceptable guiding error. A steady offset from the lock position is not counted, because it moves the whole image and does not blur a star. `cleanFraction` is the chance that an exposure of that length, started at a random moment during guiding, would have had clean guiding throughout. `lostFraction`, `jumpFraction` and `wobbleFraction` say how many windows each cause spoiled, and one window can have several causes. `longestReliableExposureSeconds` is the longest length with at least half the windows clean. It is a model of the exposures, not a measurement of the real frames.
- **Per-run results** (`runs`). The same measurements for each run, with the sky position it began at (altitude, azimuth, declination, pier side). Later analysis can compare sky position with guiding quality across nights.

## Who reads it

Post-processing compares the guiding error with the acceptable guiding error.

For exact behavior, read the code. The code is the source of truth.
