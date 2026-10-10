# Pre-processing: is the capture data good?

`assess_capture_input_quality.py` looks at how the night's frames were gathered. It runs before anyone interprets the frames, because a night whose exposures never reached the frame library describes only part of the night. The result is a `CaptureInputQuality`.

## What it measures

- **Coverage** (`lightFrames`, `spectralFrames`, `imagingFrames`). Light frames in the library for this night and this equipment. `hasEnoughFrames` is false below 10, and then the later stages are skipped.
- **Captures without a frame** (`capturesWithoutFrame`). Light exposures Ekos finished that have no frame in the library. The frame was not downloaded, not sorted into a target, or deleted. Ekos logs the time an exposure ends, and each frame records when it began, so the two are matched by time: each exposure takes the nearest frame within 30 seconds, and a frame is used once. Real nights show them 2 seconds apart. Ekos saves each kind of exposure in its own folder, so the saved path tells a light from a dark, bias or flat. Calibration exposures are not expected among the lights and are counted in `ekosCalibrationCaptures`.
- **Unclassified captures** (`capturesWithoutFrameOfUnknownKind`). Exposures with no frame whose saved path Ekos did not log. They may be calibration frames, or shots that were never saved, such as focusing and alignment exposures. They are reported and never counted as missing.
- **Frames without a capture** (`framesWithoutCapture`). Library frames no Ekos exposure accounts for. A high number means the Ekos log is incomplete, not that the frames are bad.
- **Cancelled exposures** (`abortedCaptures`, `abortFraction`, a share from 0 to 1). Exposures Ekos cancelled divided by all it started. `hasFrequentAborts` is true when the share exceeds the level that 90 percent of this equipment's earlier nights stayed under.
- **Missing measurements** (`framesMissingMeasurements`). For each measurement later steps use (saturated-pixel fraction, background, pixel scale, altitude, sensor temperature), how many frames lack it. Star width and roundness are not counted, because the science library measures them only for frames it stacked.
- **Sensor temperature** (`sensorTemperatureSpreadC`, `framesOutsideDarkTolerance`). The spread in degrees Celsius between the coldest and warmest frame, and how many frames differ from the night's median by more than the dark tolerance. A dark frame calibrates a light well only within that range.

## Who reads it

Post-processing turns the verdicts into advice. Processing reads `hasEnoughFrames` to decide whether to run.

For exact behavior, read the code. The code is the source of truth.
