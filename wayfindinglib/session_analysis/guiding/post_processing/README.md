# Post-processing: what should change?

`recommend_for_guiding.py` reads the pre-processing verdicts and the processing measurements and says what, if anything, to change. The result is a list of `Recommendation` records, most serious first.

Each recommendation has:

- a **kind** and a **severity**: `info` (a finding, nothing to do), `advice` (a change that would improve results) or `warning` (the night's data is unreliable or the equipment is misconfigured);
- a plain-language **message**;
- the **evidence** it rests on and the **limit** it was compared with, so a reader can check it;
- a **confidence**: `high`, `medium` or `low`. It is `medium` when the limits come from equipment that only partly matches the night's. It is `low` for a guiding-error conclusion when the guide signal was poor, because then the error numbers describe a bad measurement, not the mount.

## Rules

Applied in this order:

1. Fewer than 100 measured samples: say so and stop. Nothing else can be judged.
2. A calibration that measured an impossible mount speed: warn, and advise recalibrating under a clear sky with a bright, well-focused guide star.
3. A weak or dim guide star, many lost frames, or frequent excursions: warn, and list which checks failed. For a dim star the message says how many times fainter it was than usual and how much of that a shorter guide cycle could explain. The message names the usual causes (guide camera exposure or gain too low, poor guide focus, dew or haze, cloud, a dim guide star) and says the logs cannot tell which applies.
4. Guide optics in the log that differ from the configuration: warn, and advise updating the configuration.
5. Guiding error against the acceptable guiding error: advice if it is above (with the star widening it causes), information if it is within.
6. The longest exposure length in use against the exposure-length view: advice if fewer than half of the exposures of that length would have had clean guiding. The message names the main cause, the share of windows it spoiled, and the longest length that would have worked. Confidence is `medium`, or `low` when the guide signal was poor.

The summary's `flagged` field is true when any recommendation is a warning, and `flagReasons` lists their kinds.

For exact behavior, read the code. The code is the source of truth.
