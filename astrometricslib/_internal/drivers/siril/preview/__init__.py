"""Purpose: Siril's steps for the stack preview.

Description: The modules of the preview part of the Siril driver:

- `color_calibration`: Siril's photometric colour calibration (PCC).
- `stretch`: the stretch, the green removal and saving the FITS and JPEG.
- `starnet`: splitting a picture into stars and the rest with StarNet.

Each module writes Siril's commands; `core.script_run` runs them.
"""
