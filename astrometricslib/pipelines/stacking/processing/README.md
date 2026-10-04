# Processing

This step lines up and combines frames. Siril stacks the individual frames (see `stack_runner.py` one level up). The files here handle what Siril does not: stacks of different exposure lengths and spectral frames.

## What it does

- `exposure_groups.py` splits frames by exposure length, then combines the per-group stacks into one image. Each group is weighted by its noise, and a group is left out where it is saturated or clipped at zero. `saturated_pixel_mask` marks those pixels, so a later check can tell which stars had their cores replaced.
- `group_alignment.py` finds the shift between group stacks, by the zero-order star or by phase correlation, and moves them into line. For imaging stacks it then refines the shift: it matches the stars of the two stacks and fits a shift, a rotation and a scale about the image centre (`refine_alignment_with_stars`). Stacks from different nights differ by a small rotation and scale, which a shift cannot remove. On M 57 and M 27 the stars of two group stacks were 0.6 to 1.1 pixels apart on average after the best shift (up to 1.9 pixels toward the frame edges), and the fit brought that to 0.04 to 0.06 pixels.
- `spectral_frame_alignment.py` aligns spectral frames that have no usable star pattern.
- `group_derotation.py` turns spectral group stacks to one common trail tilt.

The refinement keeps the plain shift when it cannot trust the fit: fewer than 30 matched stars, a rotation over 0.5 degrees, a scale off by more than 0.5%, or stars that still miss by more than 0.5 pixel. The group manifest records the result for each group: `alignment_rotation_degrees`, `alignment_scale`, `alignment_star_pairs` and `alignment_residual_pixels`. Spectral stacks keep the plain shift.

Alignment accuracy matters most here. On the groups measured, these steps place frames within 0.05 pixels, while Siril's registration of the same groups was 0.06 to 3 pixels off. For that reason these steps stay in Python.

For exact behavior, read the code.
