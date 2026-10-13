# Pre-processing: is the guiding data good?

`assess_guiding_input_quality.py` looks at how the night's guiding data was gathered. It runs before anyone interprets the guiding error, because a night with a lost guide star or a bad calibration produces error numbers that say little about the mount.

Each check compares the night with a limit worked out from the equipment (see `wayfindinglib/analytics/performance_envelope.py`). A check whose limit is not yet known reports `None`: the night is neither passed nor failed. The result is a `GuidingInputQuality`.

## What it measures

- **Lost fraction** (`lostFraction`, a share from 0 to 1). The guider's frames that it marked as failed, for example because it lost the star, divided by all its frames. Lost frames never become samples, so they are counted from the guiding run records. A high value means the guider often had no correction to make. `hasHighLoss` is true when the share exceeds the level that 90 percent of this equipment's earlier nights stayed under.
- **Excursion fraction** (`excursionFraction`, 0 to 1). The share of samples whose total error exceeds the excursion limit. An excursion is an event, such as a lock on the wrong star, not ordinary noise. The limit is the larger of five times the acceptable guiding error and one guide pixel. `hasFrequentExcursions` compares the share with the equipment's earlier nights.
- **Median guide-star SNR** (`medianSnr`). The signal-to-noise ratio of the guide star. A low value means the star was faint, out of focus, or seen through haze. `hasLowSignal` is true when it falls below the level that is unusual for this equipment's own history.
- **Median guide-star brightness** (`medianStarMass`, in camera counts, with `starMassLimit` and `typicalStarMass`). The total light the guide star delivered, from the guide log's star mass. Unlike the SNR it does not depend on the noise, so it shows directly how much light reached the guide camera. A guide exposure cut in half halves it, and a drop far bigger than the change in guide cycle has another cause. `hasDimStar` is true when it falls below the level that is unusual for this equipment's own history. `typicalCadenceSeconds` is the equipment's usual guide cycle, so the two can be compared.
- **Calibration problems** (`calibrationProblems`, sentences). Runs whose calibrated mount speed is not positive or is above the sidereal rate, which is how fast the sky turns (15.04 arcseconds per second). A mount's guide speed is a fraction of that rate, so a faster measured speed means the calibration went wrong. This check needs no equipment history.
- **Guide scale agreement** (`guideScaleMatchesConfiguration`). Whether the plate scale in the guide log matches the configured guide optics. A mismatch means the configuration describes different equipment than the night used.
- **Coverage.** Runs, guided seconds, samples, and the guide cadence (the median time between samples). `hasEnoughSamples` is false below 100 samples, and then the later stages are skipped.

## Who reads it

Processing uses the excursion limit to decide which samples to leave out of the guiding error. Post-processing turns the verdicts into warnings.

For exact behavior, read the code. The code is the source of truth.
