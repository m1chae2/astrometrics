# Processing: what do the frames show?

`measure_capture_performance.py` turns the night's light frames into the numbers that describe what they show. The result is a `CapturePerformance`.

## What it measures

- **Clipping** (`clipping`, one entry per target and exposure length). Whether a star clips at the camera's ceiling. Each entry gives the frames with a measured value, how many clipped, the share, and `isClipped`. When the science library has stacked the target, `isClipped` is its verdict (`basis` is `science_stack`) and `scienceRecommendedExposureSeconds` is the exposure it says would keep the brightest star below the ceiling. Otherwise the frames decide (`basis` is `frame_pixel_count`): a frame clips when at least 4 pixels are saturated, and an exposure length clips when at least half its frames do. One clipped frame in a group can be a cosmic ray or a satellite trail and says nothing about the exposure length. Exposure lengths that differ only by rounding share a group. Spectroscopy and imaging frames are kept apart.
- **Star quality** (`starQuality`). The median star width in arcseconds and the median roundness of the night's imaging frames, with the limits from the equipment's earlier nights. Roundness is the narrow axis divided by the wide one, so 1 is a circle. Only frames long enough for guiding error to show in them count (three guide cycles of the equipment's own guider), and only frames the science library registered, since registration measures the stars. The medians need at least 10 such frames.
- **Efficiency** (`efficiency`). Total exposure time, the span from the first frame's start to the last frame's end, and their ratio, the `dutyCycle`. The rest of the span went to readout, slews, focusing, guiding set-up, calibration frames and waiting. It is information only; no limit is applied.

## Who reads it

Post-processing compares the clipping and the star quality with their limits.

For exact behavior, read the code. The code is the source of truth.
