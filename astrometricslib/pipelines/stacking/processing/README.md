# Processing

This step lines up and combines frames. Siril stacks the individual frames (see `stack_runner.py` one level up). The files here handle what Siril does not: stacks of different exposure lengths and spectral frames.

## What it does

- `exposure_groups.py` splits frames by exposure length, then combines the per-group stacks into one image. Each group is weighted by its noise, and a group is left out where it is saturated or clipped at zero.
- `group_alignment.py` finds the shift between group stacks, by star positions or by phase correlation, and moves them into line.
- `spectral_frame_alignment.py` aligns spectral frames that have no usable star pattern.
- `group_derotation.py` turns spectral group stacks to one common trail tilt.

Alignment accuracy matters most here. On the groups measured, these steps place frames within 0.05 pixels, while Siril's registration of the same groups was 0.06 to 3 pixels off. For that reason these steps stay in Python.

For exact behavior, read the code.
