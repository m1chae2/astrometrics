# Stacking processing

This stage lines up and combines frames. Siril stacks the individual frames (see `stack_runner.py` one level up). The files here handle what Siril does not: stacks of different exposure lengths and spectral frames.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

These terms appear throughout:

- An *exposure group* is the frames of one stack that share one exposure length. Each group is stacked on its own and the group stacks are then combined.
- A *spectral frame* is a frame taken through a grating with no slit (a slitless spectrum), which spreads each star's light into a spectrum trail of colors. The *zero order* is the plain, undispersed image of the star that such a frame also records. [Finding the zero-order target](#finding-the-zero-order-target) defines it in full.
- *Registration* or *alignment* is lining frames up so the same star falls on the same pixel.
- The *reference group* is the group that carries the most weight when the groups are combined. Each group is weighted by its noise.

The steps are:

1. `exposure_groups.py` splits frames by exposure length. Then `group_combination.py` combines the per-group stacks into one image. It weights each group by its noise and leaves a group out where it is saturated or clipped at zero. It also puts every group on the brightness scale of the reference group (see [the brightness scale between exposure groups](#the-brightness-scale-between-exposure-groups)). `saturated_pixel_mask` in `group_gains.py` marks those pixels, so a later check can tell which stars had their cores replaced.
    - **How the noise of one pixel is measured.** `estimate_background_noise` takes the differences between neighboring pixels, finds a rough scale from their median absolute deviation, keeps the differences within 4 times that scale (`NOISE_CLIP_SIGMAS`; star pixels fall outside), and takes their standard deviation divided by √2. The median alone is too coarse for a camera that stores values in steps, such as the ASI533MM in steps of 4 counts, because every difference is then a multiple of the step. The standard deviation uses every kept value, so it is fine-grained. On synthetic noise of 8 to 40 counts stored in steps of 1 and 4, the result was within 0.1% of the true value, with a spread of 0.12% between images (synthetic check in `test_exposure_groups.py`, not yet on real frames; design estimate).
2. `siril_group_alignment.py` lines up the group stacks with Siril's plate solutions, and `group_alignment.py` lines up the groups Siril cannot use (see [how group stacks are lined up](#how-group-stacks-are-lined-up)).
3. `spectral_frame_alignment.py` aligns spectral frames that have no usable star pattern.
4. `group_derotation.py` turns spectral group stacks to one common trail tilt, the angle at which the spectrum trail crosses the frame.

### How group stacks are lined up

Every group stack is lined up with the reference group. The method depends on what the stack shows.

1. **Imaging stacks: Siril's plate solutions.** Siril matches the stars of each group stack to the Gaia catalog and fits the lens distortion (`seqplatesolve`, order 3), then moves every stack onto the reference's grid (`seqapplyreg`). The Siril driver does this (`drivers/siril/stacking/group_registration.py`), and `siril_group_alignment.py` turns the result into the lined-up image, a mask of the pixels that hold data and an `AlignmentResult`. The move covers any shift, rotation (the half turn of a meridian flip too), scale and lens distortion, so no setting of ours decides it. On the NGC 2244, NGC 7023 and sparse-field groups tested, the leftover offset was as small as the earlier star-pattern code gave or smaller, in a third to a twelfth of the time, and the 10, 20 and 30 s groups of M 31, which the old code left out, were lined up (measured; see [STK-64](../../../../documentation/library_design/validation/stacking.md#stk-64)).
    - **When Siril cannot solve a group.** The group is left out, and its left-out reason gives Siril's own account (for example, how many stars Siril found). There is no other method for an imaging group. One group's failure does not stop the others: Siril reports the group that did not solve and goes on. If the reference group is the one that fails, no group can be lined up with it, so the reference stands alone and the others are left out.
    - **What it records.** The group manifest records `alignment_method`, `alignment_stars_found`, `alignment_matched_stars` (the stars Siril matched to Gaia), `alignment_fit_residual_pixels` and `alignment_fit_residual_arcsec` (how far a matched star lies from its catalog position, root mean square), `alignment_fit_order`, `alignment_rotation_degrees`, `alignment_scale` and `alignment_failure_reason`. These come from Siril's own output, not from pairs of stars between two stacks.
2. **Spectral stacks: Siril where it solves, the zero-order star elsewhere.** Siril solves the stacks first. A spectral stack of a bright star that holds too few stars (the shortest exposures, with 1 to 14 stars) cannot be solved. Siril's own answer for each group decides which way the group goes, not a star count of ours. The group is then shifted so its zero-order star lands on the reference's (`measure_alignment` with `prefer_star_position`, see [finding the zero-order target](#finding-the-zero-order-target)), the offset is trusted when the images correlate at least 0.3 after the move (measured; see [STK-13](../../../../documentation/library_design/validation/stacking.md#stk-13)), and the log and the manifest (`alignment_siril_reason`) give Siril's reason. When the reference itself is not solved, every group takes the zero-order path. The stacks are turned onto one trail tilt after Siril lines them up (see `group_derotation.py`).
3. **Bodies of the solar system: correlation.** The Moon has no stars, and Siril's command line has no registration that works on its craters (Siril 1.4.5 registration and plate-solve commands all failed on the three Moon groups). The group stacks are shifted by the offset phase correlation finds (`measure_alignment`), trusted at a correlation of 0.3 or more, with a leftover of about 1 pixel (partly measured; see [STK-64](../../../../documentation/library_design/validation/stacking.md#stk-64)).

Earlier versions refined a shift with matched stars (partly measured; retired; see [STK-14](../../../../documentation/library_design/validation/stacking.md#stk-14)), searched star patterns when correlation failed (partly measured; retired; see [STK-15](../../../../documentation/library_design/validation/stacking.md#stk-15)) and judged that Siril's registration was worse (measured; retired; see [STK-16](../../../../documentation/library_design/validation/stacking.md#stk-16)).

`GroupPositionMap` (`group_position_map.py`) describes either kind of move the same way: for each pixel of the common grid, where to read the group's own stack. The group's rejection map is moved with it, and the edge trim counts frames with it.

Pixel rejection (throwing out a pixel's value in one frame when it disagrees strongly with the other frames) when Siril combines the frames is set before this stage runs. The limits come from `rejection_bounds` in `utilities/rejection_thresholds.py`. The [stacking README](../README.md#pixel-rejection-limits) describes them (partly measured; see [STK-08](../../../../documentation/library_design/validation/stacking.md#stk-08)).

### Finding the zero-order target

The zero order is the undispersed image of the target in a slitless spectrum frame. Three functions in `group_alignment.py` find it. Each returns a position as (row, column), in pixels, or `None` when it finds no clear target.

1. `find_zero_order_position` looks for a point source. It smooths the image by 2 pixels (design estimate; see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#design-estimates)) and takes the brightest spot within 150 pixels of the image center. It accepts the spot only when no spot more than 40 pixels away is brighter than half of it. The position is good to a fraction of a pixel. The stack aligner and the trail-tilt measurement use this function.
    - **Why these numbers.** The spectroscopy target is framed near the image center, and 150 pixels stays well inside the distance to the spectrum trail. A second spot at least half as bright (a double star, or a trail as bright as the star) makes the choice ambiguous. The function then returns `None` and the caller correlates the images instead. The 150 pixel window is partly measured (see [STK-17](../../../../documentation/library_design/validation/stacking.md#stk-17)). The 40 pixel distance and the one-half fraction are design estimates (see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#design-estimates)).
2. `find_extended_zero_order_position` looks for a broad glow, such as a globular cluster, which has no single bright spot. It follows these steps:
    - It averages the image in 4 by 4 pixel blocks and subtracts the sky (the median of the blocks).
    - It smooths with a Gaussian of 25 pixels.
    - It takes the brightest local maximum within 150 pixels of the image center. A local maximum is a pixel no lower than any pixel within 50 pixels of it.
    - It compares that peak with the next brightest local maximum within 300 pixels of the center, and accepts the peak only when the rival is at most one third of its height.
    - It returns the brightness-weighted center of the smoothed pixels that are at least half the peak height.
3. `locate_zero_order` runs function 1 and, if that returns `None`, function 2. It returns a `ZeroOrderPosition` with the row, the column and `is_extended_target`. `is_extended_target` is `True` when function 2 supplied the position. An extended-target position marks the middle of a glow tens of pixels wide, so it is good to a few pixels and no better. Callers that need sub-pixel accuracy, such as alignment on the star, should keep using function 1.

**Quality values.** The rival fraction is the rival height divided by the peak height, both above the sky, with no units. It decides the second search. A value near 0 means one clear glow. A value near 1 means two similar glows, and the search returns `None`. The functions do not reliably find a target more than about 150 pixels off the frame center. The limit of one third, the 25 pixel smoothing and the 300 pixel rival window are partly measured (see [STK-18](../../../../documentation/library_design/validation/stacking.md#stk-18)).

`measure_spectral_frame_file` (in `pipelines/spectroscopy/pre_processing/spectral_frame_check.py`) uses `locate_zero_order` and reports the result as `zero_order_source`: `point source` or `extended target`. A caller who knows where the target is can pass the position and skip the search, and the source is then `given`.

### The brightness scale between exposure groups

Siril leaves each group stack with its own overall brightness. Before the stage averages the groups, `group_combination.py` divides each group's counts-per-second image by a group scale factor (a single number) so that all groups read the same. A counts-per-second image is the raw camera counts divided by the exposure time. The code and the quality summary call this number a "gain" (for example `gainMidRange`). It is not the camera's gain setting.

**How the group scale factor is measured.** For every group except the reference group, the stage measures the same isolated stars in both groups (`measure_star_flux_ratios`):

1. It finds up to 3,000 stars in the reference and keeps those with no other detected star within 25 pixels.
2. It leaves out a star with any pixel in its circle or sky ring that is uncovered, saturated, or above half the camera's clip ceiling (`star_clip_level`). The ceiling comes from the camera profile (`clip_ceiling_adu`, the highest raw count, in ADU or analog-to-digital units, that a saturated pixel reaches). Stars near the ceiling read low, which biases the scale factor.
3. It measures each star's flux (its total light) with `measure_aperture_photometry`. Aperture photometry sums the light in a circle around the star and subtracts the sky measured in a ring around it. Here the circle has a 14 pixel radius, and the sky is the median of the ring from 19 to 25 pixels. The circle is wide enough that a star a pixel wider in one group keeps the same share of its light.
4. It measures the noise a flux ratio carries and keeps stars at least 10 times above that noise (`STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE`; measured; see [STK-19](../../../../documentation/library_design/validation/stacking.md#stk-19)).
    - **How the noise is measured.** It subtracts the reference (times the scale factor) from the group, so the sky and the stars both groups share cancel. It then places the same circle and ring at random empty spots (empty apertures, at least twice the ring's outer radius from any detected star) and measures their flux. What is left is the noise the two groups do not share.
    - **Why faint stars are dropped.** A faint star's ratio is noisy. The stage also sorts the stars into quarters by their reference flux, which is itself noisy. The faintest quarter collects stars whose reference flux happened to read low, and their ratios read too high (measured; see [STK-19](../../../../documentation/library_design/validation/stacking.md#stk-19)).
5. The group scale factor is the median of (group flux / reference flux) over the remaining stars. The stage also takes the median over the faintest quarter and over the brightest quarter of the stars.

A group that shares fewer than 16 such stars with the reference (`STAR_GAIN_MINIMUM_STARS`; measured; see [STK-19](../../../../documentation/library_design/validation/stacking.md#stk-19)), such as a slitless spectrum with one star, falls back to the pixel method. The pixel method takes the median of (group pixel / reference pixel) over source pixels at least 10σ of the background noise above the sky. It uses only pixels between the 20th and 80th percentile of their brightness (design estimate; see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#design-estimates)), after it subtracts each group's own sky level (`estimate_sky_level`).

**Why stars, not pixels.** On a color camera's stacks the 10σ noise floor of the pixel method sits at a few percent of the sky level. The source pixels are then mostly sky gradients and differences in star width, and the pixel ratio follows the sky rather than the stars (measured; see [STK-19](../../../../documentation/library_design/validation/stacking.md#stk-19)).

**Combining.** The stage subtracts the sky from a group with a measured scale factor, divides by the factor and places the group on the reference's sky level. Its stars then match the reference and its sky does not leave a step where the weights change. The stage combines a group whose scale factor could not be measured as it is.

**The linearity check.** A camera is linear when faint and bright stars give the same ratio. The check measures how far the faint-quarter and the bright-quarter ratios sit from the scale factor, as a fraction of it, and takes the larger: `max(abs(faint-quarter ratio - scale), abs(bright-quarter ratio - scale)) / scale`. It has no units. It is the error that applying the one scale factor makes on the faint or bright stars. When it is larger than `exposure_group_gain_tolerance` in `[Processing.Siril]` (default 0.05, which is 5%), one scale factor does not describe the group. The stack runner (`stack_runner.py`) then leaves that group out of the combined image and combines the remaining groups, the same fallback it uses for a group that cannot be lined up. It never leaves out the reference group. The tolerance is partly measured (see [STK-20](../../../../documentation/library_design/validation/stacking.md#stk-20)).

**What the reason says.** The message names the ratios, how far the largest one sits from the overall ratio and the limit. It says the bright end may be clipped only when a pixel in either stack is at or above the clip level, which is half the camera's clip ceiling (`star_clip_level`). The star method never measures a star that has such a pixel, so its message never blames clipping. For example, a group whose ratio drifts from 2.087 on faint stars to 1.887 on bright stars is described as a ratio that changes with star brightness.

**A clipped reference (pixel method).** A long exposure clips first, and it is usually the reference. A clipped reference reads too low at its bright end, so a clean group then reads higher than its scale factor there. When the group reads higher, and the reference holds more pixels at or above the clip level than the group does, the stage does not leave the group out (`reference_clipped`). A group that reads lower than its scale factor at the bright end is still left out. The bright-end ratio keeps every shared pixel, because a camera's knee lies above half its ceiling. The rule is a design estimate (see [STK-20](../../../../documentation/library_design/validation/stacking.md#stk-20)).

**Spectral stacks.** A slitless spectrum has no field of stars, and its short groups are clipped at zero, so their sky per second is not a sky brightness. The stack runner passes `use_star_fluxes=False` for spectral stacks, which keeps the pixel method and skips the sky check.

**The sky check.** The stage divides each group's sky level per second by the reference's (`sky_ratio`). The stack runner then divides that ratio by the group's ISO ratio to the reference's (`normalize_sky_ratios_by_iso`), because raw counts scale with ISO and groups at different ISOs are never merged. It leaves out a group whose ISO-normalized sky is more than `SKY_RATIO_LIMIT` (3) times brighter (partly measured; see [STK-21](../../../../documentation/library_design/validation/stacking.md#stk-21)).

**The scale range.** A measured scale factor outside `PLAUSIBLE_GAIN_RANGE` (0.25 to 4), has an unknown scale. The stack runner leaves that group out with a reason that names the measured value (`describe_unbelievable_gain_group`), and the summary keeps the measured value. It does not combine the group at an assumed scale of 1. A group that shares too few stars or pixels to compare at all is still combined as it is (its scale was not measured).

### The sky background between exposure groups

Each group stack also has its own sky background. It differs from the reference group's in level, in color (each color channel has its own sky) and in tilt across the frame. A single sky number cannot remove all of that, so before the average, `group_sky_matching.py` fits a flat tilted surface (a plane) to the difference between the group and the reference, one plane for each color channel, and takes it off the group. The fit uses the median of 64 by 64 pixel blocks that both groups hold, and drops blocks that stars or the target disturb, on either side (the shared fit in `pipelines/shared/binned_sky_plane.py`). A group that overlaps the reference in fewer than 12 blocks keeps only its overall sky level, and the log says so. Spectral stacks are not matched, because a slitless spectrum has no sky field to compare.

Without this, a region seen by only one group took that group's background. On NGC 6992 (Nikon D5300, a 180 s group and a 300 s group lined up with a half turn and a shift of 527 pixels), the green channel stepped by 5% where the 300 s group's coverage began (partly measured; see [STK-D10](../../../../documentation/library_design/validation/stacking.md#design-estimates)).

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `exposure_group_gain_tolerance` in `[Processing.Siril]` | 0.05 (partly measured; see [STK-20](../../../../documentation/library_design/validation/stacking.md#stk-20)) | Largest disagreement between the faint-star and bright-star ratios before a group is left out |
| `SKY_RATIO_LIMIT` in `group_gains.py` | 3 (partly measured; see [STK-21](../../../../documentation/library_design/validation/stacking.md#stk-21)) | Brightest sky, relative to the reference group, that a group may have |
| Alignment limit in `group_alignment.py` | Correlation 0.3 (measured; see [STK-13](../../../../documentation/library_design/validation/stacking.md#stk-13)) | Decides whether a shift alignment (zero order or correlation) is trusted. A Siril alignment has no such limit: Siril either solves a stack or does not |
| `SIP_ORDER` in `drivers/siril/stacking/group_registration.py` | 3 (measured; see [STK-64](../../../../documentation/library_design/validation/stacking.md#stk-64)) | Order of the lens-distortion fit Siril makes for each group stack |

## Quality metrics

Each entry in `exposureGroups` in the quality summary holds these four fields:

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `gainMidRange` | None (a ratio of two counts-per-second values) | Outside 0.25 to 4 is a failed measurement (partly measured; see [STK-21](../../../../documentation/library_design/validation/stacking.md#stk-21)) | The group scale factor applied to the group: the median star-flux ratio (the median pixel ratio on the fallback) | People reading the summary; the UI |
| `gainBrightEndRatio` | None | None | The same ratio on the brightest quarter of the stars (the brightest 1% of pixels on the fallback). The stage never applies it | People reading the summary; the UI |
| `gainDisagreement` | Fraction (0.05 is 5%) | 0.05 (partly measured; see [STK-20](../../../../documentation/library_design/validation/stacking.md#stk-20)) | The larger distance of the faint-quarter and bright-quarter ratios from `gainMidRange`, divided by `gainMidRange` (`abs(gainBrightEndRatio - gainMidRange) / gainMidRange` on the pixel fallback). High means one scale factor does not describe the group | The linearity gate |
| `gainNonlinear` | None | Same as `gainDisagreement` | `true` when `gainDisagreement` is above the tolerance. `leftOutReason` repeats it in words | The linearity gate |

A group with too few shared pixels, or a scale factor outside 0.25 to 4, keeps a scale factor of 1 and has no `gainDisagreement`, because there is nothing to compare. A value outside that range is a failed measurement, not a real scale. The range is wide enough for the real spread and far from such failures (partly measured; see [STK-21](../../../../documentation/library_design/validation/stacking.md#stk-21)). A group that fails the check is left out, and its `leftOutReason` names both ratios. The `exposure_group_linearity` gate fails with that sentence, which flags the stack (`flagged` and `flagReasons`). The same numbers are in the manifest in the `groups` folder.

## Files

- `exposure_groups.py`: splits frames by exposure length and by night. `fold_exposure_lengths` is the one rule that says which group each exposure length lands in (a length with fewer than `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` frames joins the nearest length, when that length is within `MAXIMUM_EXPOSURE_FOLD_RATIO`; otherwise its frames are left out of the stack and the frame screening records each one with the reason). `plan_exposure_folding` returns both the groups and the left-out reasons; `fold_exposure_lengths` returns the groups alone, and `split_frames_by_exposure` calls the plan.
- `group_gains.py`: measures the group scale factor from mid-range pixels, and holds the background-noise and saturation helpers.
- `group_star_gains.py`: measures the group scale factor from isolated stars.
- `group_combination.py`: weights the groups and combines the group stacks. `combine_exposure_group_images` returns an `ExposureGroupCombination`: the image, the gain applied to each group and whether it was measured, the sky levels, the group weights, the reference group, and the trusted and covered masks.
- `group_sky_matching.py`: fits and removes the sky background difference between a group and the reference group, one plane for each color channel.
- `group_file_merging.py`: joins the per-group rejection maps and registration sequences into one file each. Each map is moved with its group's position map, resampled linearly so the fractions stay between 0 and 1.
- `siril_group_alignment.py`: lines up the group stacks with Siril's plate solutions and describes each group's result.
- `group_position_map.py`: the move of a group stack, as the place to read in the group's own stack for each pixel of the common grid.
- `group_alignment.py`: the shift alignment (zero-order star or correlation), the shift itself, the result of an alignment, and the finder of the zero-order target and of bright star centers.
- `image_registration.py`: `resample_at_positions`, which reads an image where a map points. The group position maps and the color step use it.
- `spectral_frame_alignment.py`: aligns spectral frames by phase correlation.
- `group_derotation.py`: turns spectral group stacks to one trail tilt.

For exact behavior, read the code.
