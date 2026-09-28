# Asteroid detection

This pipeline searches a sequence of images of the same field for objects that move against the fixed background stars — asteroids and other minor planets. It works by tracking dots of light that shift position, frame to frame, in a way a fixed star never does.

## What the pipeline does, in order

1. **Work out where each frame is pointing.** `frame_wcs_composer.py` estimates a per-frame World Coordinate System (WCS, the mapping from pixel position to sky position) from the mount's reported pointing, since a raw light frame usually has no plate-solved WCS of its own yet.
2. **Find every dot of light in each frame.** The pipeline reuses astrometry's own source detector (`SourceDetector`) to find point sources in every frame of the sequence.
3. **Track dots across frames and filter out noise.** `detection.py`'s `MovingObjectDetector` links up detections of the same moving object across frames and runs a cascade of checks — is it detected in more than one frame, does it move in a straight line at a constant rate, is the rate within a plausible range — rejecting a candidate at whichever check it fails. `models.moving_object.CascadeStage` records how far each candidate made it: a rejected candidate keeps the stage it was rejected at (for example `rejected_stationary_pixel`, a dead or hot pixel that never moved at all), and a surviving candidate reaches `rate_linearity_confirmed`.
4. **Check survivors against known asteroids.** `ephemeris.py`'s `EphemerisCrossMatcher` queries an external ephemeris service (SkyBoT) for known minor planets near the field and time of observation, and matches surviving candidates against them. A match reaches `ephemeris_matched`; an unmatched survivor stays at `rate_linearity_confirmed` — a real moving object, but not yet identified as a specific known body.
5. **Look for a rotation period, when enough data exists.** `rotation_period.py` searches a tracked object's brightness history for a repeating pattern, the way an asteroid's periodic brightness variation reveals its rotation.

## Where each piece lives

- `pipeline.py` is the coordinator: it ties together WCS estimation, detection, the rejection cascade, and ephemeris matching for one run.
- `runner.py` sits one level above `pipeline.py`. It runs the pipeline across a target's stored images and writes the results back onto the target's record.
- `detection.py`, `ephemeris.py`, `frame_wcs_composer.py`, `rotation_period.py` each implement one step above.

## A note on scope

This pipeline finds and classifies moving objects; it does not stack, plate-solve, or catalog-identify the fixed stars in the same images — it depends on astrometry's per-frame star detection but does not duplicate astrometry's own plate-solving.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
