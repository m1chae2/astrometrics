# Pre-processing: can the data answer the question?

`assess_sky_input_quality.py` counts how much of the sky the recorded nights cover. The result is a `SkyInputQuality`.

## What it measures

- **Nights** (`nights`, `firstNight`, `lastNight`). Nights with at least one measurement that has a position. `hasEnoughData` is false below five nights, and then the later stages are skipped.
- **Samples by metric** (`samplesByMetric`). Measurements of each kind with a position, and `samplesWithoutPosition`, the ones left out because the frame or run has none.
- **Coverage** (`coverage`). For every part of the sky the telescope is configured to reach, the measurements and the distinct nights it has. Altitude bands wholly outside the telescope's configured altitude range are not expected. Star roundness is not counted, because it comes from the same frames as star width.
- **Gaps** (`gaps`). Parts of the sky reached on fewer than three nights. A nearly empty region is a region the telescope has not been tried in, and it is not a region that performed well.
- **Guiding nights excluded** (`guidingNightsExcluded`). Nights whose guiding runs were left out because the night had an impossible calibration or a weak guide star. Their error numbers describe a bad measurement, not the mount.

For exact behavior, read the code. The code is the source of truth.
