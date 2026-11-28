"""Purpose: What every Siril run needs, whatever it does.

Description: The modules of the driver's core:

- `program`: finding Siril (Flatpak or native, Linux or Windows) and building
  its command lines.
- `settings`: the app's private Siril settings file.
- `lock`: the machine-wide Siril slot every run takes.
- `script_run`: a short script run to the end (previews, library scripts).
- `session`, `pipes`: the long pipe session a stack runs in.
- `version`: Siril's version.
- `output_parsing`: reading what Siril prints and writes into numbers and
  warnings.
"""
