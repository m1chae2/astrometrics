# Stacking

This pipeline combines many individual pictures (sub-exposures) of the same target into one master image. Adding aligned pictures together makes real stars and galaxies brighter, while noise that occurs randomly in each picture — camera read noise, heat noise — tends to cancel out. Before combining, the pipeline also rejects temporary artifacts such as satellite trails, cosmic rays, and hot pixels.

## What the pipeline does, in order

1. **Group frames by exposure length.** Frames taken with different exposure times cannot be combined directly, since a longer exposure collects more light per pixel. The pipeline sorts frames into exposure groups, stacks each group on its own, then combines the group stacks so no frame's light is wasted.
2. **Check that frames within a group agree.** Two homogeneity checks run before stacking: one compares each frame's background brightness against the rest of the group (`background_homogeneity.py`) to catch a frame taken through cloud or twilight, and one checks that gain settings match across the group's frames (`frame_homogeneity.py`). A frame that disagrees with the group is excluded rather than stacked.
3. **Line up the frames.** Frames must be aligned pixel-for-pixel before combining. `group_alignment.py` aligns each exposure group's stack; `spectral_frame_alignment.py` aligns a batch of already-calibrated spectral frames without star detection, since a dispersed spectrum has no normal point-source star pattern to align on; `group_derotation.py` lines up spectral exposure-group stacks onto one common trail tilt.
4. **Run the external stacker.** `siril_stacking.py` drives Siril, the external program that performs the pixel-rejection and combination, with the two extra safeguards spectroscopy needs (its dispersed images cannot use Siril's normal star-based registration).
5. **Judge the result.** `stack_quality.py` holds the rules for deciding whether a stacked image is good enough to use. `exposure_saturation.py` checks whether a given exposure length saturates the brightest star in the field, and `exposure_group_report.py` records a description of the exposure groups a stack was built from.
6. **Save a picture of the stack.** `stack_preview.py` saves a JPEG next to the finished stack, named `<stack name>_preview.jpg`. A stack holds linear data, which looks almost black on a screen, so two steps run on a scratch copy first:
    1. GraXpert removes the sky's brightness gradient, if the `[Processing.GraXpert]` setting names a GraXpert command. Its AI model learns what the smooth background looks like and subtracts it. Leaving the setting blank skips this step.
    2. Siril's Autostretch sets the black point from the image's own background noise and bends the brightness curve so faint stars and nebulae show, the same view Siril itself gives.

   The JPEG is for people only. Nothing in the pipeline reads it, and the stack file is not changed, so photometry and spectroscopy still read the linear data. If GraXpert fails, the picture is made without the gradient step. If Siril is missing or fails, the pipeline logs a warning and keeps the stack. The step removes any older preview first, so a preview always matches the current stack. The image viewer's Stretch view shows this JPEG when it is at least as new as the stack.

## Where each piece lives

- `stage.py` is the coordinator: it runs the steps above in order for one target, and is what the rest of the codebase calls to stack a target's frames.
- `exposure_groups.py`, `background_homogeneity.py`, `frame_homogeneity.py`, `group_alignment.py`, `spectral_frame_alignment.py`, `group_derotation.py` each implement one step above.
- `siril_stacking.py` runs the Siril stack. It is the only file that stacks pixels.
- `stack_quality.py`, `exposure_saturation.py`, `exposure_group_report.py` judge input or output quality; none of them stack pixels themselves.
- `stack_preview.py` makes the JPEG picture of a finished stack (step 6). It starts GraXpert and Siril as separate programs, each under the shared Siril slot limit.

## A note on scope

This pipeline produces one combined image (or one per exposure group/camera setup) from a target's raw frames. It does not detect stars, solve the sky position, or measure brightness — those are astrometry and photometry, which read this pipeline's output rather than duplicate its work.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
