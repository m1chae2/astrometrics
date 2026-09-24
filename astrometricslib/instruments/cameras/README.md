# Camera profiles

One JSON file per camera model. The processing code looks a camera up by
name (see `astrometricslib/drivers/camera_profile_store.py`) and gets back
the facts it needs, such as the pixel value where the sensor clips.

The layout of a file is defined by `CameraProfile` in
`astrometricslib/models/camera_profile.py`. Unknown keys are rejected, so a
misspelled key fails loudly instead of being ignored.

## Where a value belongs

* Here: facts about the camera model that do not change from one machine
  to the next, such as the clip ceiling and the sensitivity curve.
* In `astrometrics.config`: things tied to this machine or set by
  calibration, such as pixel size, sensor size and grating geometry.

A value lives in exactly one of the two places.

## Provenance

Every number says where it came from:

* `datasheet`: read from a manufacturer's document or graph.
* `measured`: measured by someone, on our own frames or in a paper.
* `assumed`: nobody has checked it. Prefer replacing these with measured
  values when the chance comes up.

## Adding a camera

1. Copy `generic_unknown_camera.json` to a new file named after the camera.
2. Change `camera_name`, set `is_generic_fallback` to `false`, and list any
   other spellings of the name (for example the FITS header's) in
   `name_aliases`. Case, spaces and punctuation are ignored when names are
   compared, so `ZWO ASI533MM Pro` and `ZWO ASI 533MM Pro` are the same.
3. Fill in each number with its provenance.

Exactly one file must have `is_generic_fallback` set to `true`. It is used
for any camera that has no file of its own, and a warning is logged the
first time each such name is seen.
