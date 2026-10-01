# Post-processing: what should change?

`recommend_for_capture.py` reads the pre-processing verdicts and the processing measurements and says what, if anything, to change. The result is a list of `Recommendation` records, most serious first. They have the same fields as the guiding recommendations: a kind, a severity (`info`, `advice`, `warning`), a message, the evidence, the limit it was compared with, and a confidence.

## Rules

Applied in this order:

1. Fewer than 10 light frames: say so and stop. Nothing else can be judged.
2. Light exposures Ekos finished that have no frame in the library: advise downloading or sorting them.
3. Frames missing measurements: advise re-scanning them and checking the camera's FITS header.
4. More cancelled exposures than 90 percent of this equipment's earlier nights: advise checking the session log.
5. Sensor temperatures too far apart for one set of darks: information, with advice to take darks at several temperatures.
6. A spectroscopy star that clips: advise a shorter exposure. If every length tried clipped, go below the shortest. If some did not, name the longest that stayed clear and how many frames were spent on lengths that clipped. The science library's recommended exposure is quoted when it has one. The message adds "if that star is the target", because in a nebula field the clipped star may be a foreground star. Confidence is `high` when the verdict is the science library's and `medium` when it came from frame counts.
7. Star width above, or roundness below, the equipment's earlier nights: warn. The message lists the usual causes (seeing, focus drift, tracking error, trailing) and points to the autofocus record and the guiding analysis, because the frames cannot tell which applies. With too few earlier nights, it states the width and says so.
8. If none of the above applied: a note that the night was within limits.

The summary's `flagged` field is true when any recommendation is a warning, and `flagReasons` lists their kinds.

For exact behavior, read the code. The code is the source of truth.
