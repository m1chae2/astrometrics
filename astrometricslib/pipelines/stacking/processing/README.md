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

1. `exposure_groups.py` splits frames by exposure length, then combines the per-group stacks into one image. It weights each group by its noise and leaves a group out where it is saturated or clipped at zero. It also puts every group on the brightness scale of the reference group (see [the brightness scale between exposure groups](#the-brightness-scale-between-exposure-groups)). `saturated_pixel_mask` marks those pixels, so a later check can tell which stars had their cores replaced.
    - **How the noise of one pixel is measured.** `estimate_background_noise` takes the differences between neighboring pixels, finds a rough scale from their median absolute deviation, keeps the differences within 4 times that scale (`NOISE_CLIP_SIGMAS`; star pixels fall outside), and takes their standard deviation divided by √2. The median alone is too coarse for a camera that stores values in steps, such as the ASI533MM in steps of 4 counts, because every difference is then a multiple of the step. The standard deviation uses every kept value, so it is fine-grained. On synthetic noise of 8 to 40 counts stored in steps of 1 and 4, the result was within 0.1% of the true value, with a spread of 0.12% between images (synthetic check in `test_exposure_groups.py`, not yet on real frames; design estimate).
2. `group_alignment.py` lines up the group stacks in three sub-steps, described in [how group stacks are lined up](#how-group-stacks-are-lined-up).
3. `spectral_frame_alignment.py` aligns spectral frames that have no usable star pattern.
4. `group_derotation.py` turns spectral group stacks to one common trail tilt, the angle at which the spectrum trail crosses the frame.

### How group stacks are lined up

`group_alignment.py` lines each group stack up with the reference group. It works in three sub-steps.

1. **Find the shift.** It finds the shift between the two stacks, by the zero-order star or by phase correlation (a method that finds the shift between two images from their Fourier transforms). The shift is trusted when, after the move, the two images correlate at least 0.3. The correlation is 1 for identical images and near 0 for unrelated ones (measured; see [STK-13](../../../../documentation/library_design/validation/stacking.md#stk-13)).
2. **Refine the shift with stars (imaging stacks only).** It matches the stars of the two stacks and fits a shift, a rotation and a scale about the image center (`refine_alignment_with_stars`). Stacks from different nights differ by a small rotation and scale, which a shift cannot remove (partly measured; see [STK-14](../../../../documentation/library_design/validation/stacking.md#stk-14)).
    - **When it keeps the plain shift.** The refinement keeps the plain shift when it cannot trust the fit. It distrusts the fit with fewer than 30 matched stars, a rotation over 0.5°, a scale off by more than 0.5%, or stars that still miss by more than 0.5 pixel (partly measured; see [STK-14](../../../../documentation/library_design/validation/stacking.md#stk-14)). Spectral stacks keep the plain shift.
    - **What it records.** The group manifest records the result for each group: `alignment_rotation_degrees`, `alignment_scale`, `alignment_star_pairs` and `alignment_residual_pixels`.
3. **Fall back to star patterns.** When correlation fails, or its shift is too far off for the stars to pair up, the stage lines the stacks up from patterns of stars instead (`find_alignment_from_star_patterns`, with `star_pattern_candidates.py`).
    - **How it works.** Small triangles of stars have a shape that does not change with shift, rotation or scale. Matching triangles gives a first guess with no starting offset.
    - **When it is kept.** The stage keeps the guess only if it lines up at least 30 stars, with a scale within 0.5% of one and a leftover under 1.5 pixels. Unlike the refinement in sub-step 2, this search allows any rotation. That includes the half turn of a mount flip at the meridian, when the telescope mount turns the camera over as the target crosses the north-south line overhead.
    - **Status.** Partly measured; see [STK-15](../../../../documentation/library_design/validation/stacking.md#stk-15).

Alignment accuracy matters most here. These steps place group stacks more accurately than Siril's registration of the same groups, so they stay in Python (measured; see [STK-16](../../../../documentation/library_design/validation/stacking.md#stk-16)).

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

Siril leaves each group stack with its own overall brightness. Before the stage averages the groups, `exposure_groups.py` divides each group's counts-per-second image by a group scale factor (a single number) so that all groups read the same. A counts-per-second image is the raw camera counts divided by the exposure time. The code and the quality summary call this number a "gain" (for example `gainMidRange`). It is not the camera's gain setting.

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

**Spectral stacks.** A slitless spectrum has no field of stars, and its short groups are clipped at zero, so their sky per second is not a sky brightness. The stack runner passes `use_star_fluxes=False` for spectral stacks, which keeps the pixel method and skips the sky check.

**The sky check.** The stage divides each group's sky level per second by the reference's (`sky_ratio`). It leaves out a group whose sky is more than `SKY_RATIO_LIMIT` (3) times brighter (partly measured; see [STK-21](../../../../documentation/library_design/validation/stacking.md#stk-21)).

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `exposure_group_gain_tolerance` in `[Processing.Siril]` | 0.05 (partly measured; see [STK-20](../../../../documentation/library_design/validation/stacking.md#stk-20)) | Largest disagreement between the faint-star and bright-star ratios before a group is left out |
| `SKY_RATIO_LIMIT` in `exposure_groups.py` | 3 (partly measured; see [STK-21](../../../../documentation/library_design/validation/stacking.md#stk-21)) | Brightest sky, relative to the reference group, that a group may have |
| Alignment limits in `group_alignment.py` | Correlation 0.3 (measured; see [STK-13](../../../../documentation/library_design/validation/stacking.md#stk-13)); 30 stars, 0.5°, 0.5%, 0.5 pixel (partly measured; see [STK-14](../../../../documentation/library_design/validation/stacking.md#stk-14)) | Decide whether an alignment is trusted |

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

- `exposure_groups.py`: splits frames by exposure length, measures the group scale factor and combines the group stacks. `fold_exposure_lengths` is the one rule that says which group each exposure length lands in (a length with fewer than `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` frames joins the nearest length), and `split_frames_by_exposure` calls it. `combine_exposure_group_images` returns an `ExposureGroupCombination`: the image, the gain applied to each group and whether it was measured, the sky levels, the group weights, the reference group, and the trusted and covered masks.
- `group_alignment.py`: lines up the group stacks and finds the zero-order target.
- `star_pattern_candidates.py`: the star-pattern search that `group_alignment.py` falls back to.
- `spectral_frame_alignment.py`: aligns spectral frames by phase correlation.
- `group_derotation.py`: turns spectral group stacks to one trail tilt.

For exact behavior, read the code.
