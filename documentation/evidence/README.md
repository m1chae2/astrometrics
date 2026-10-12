# Evidence

This folder holds measurement records. Comments in the code cite them as the source of a number, for example a threshold or a default value.

- Files are kept exactly as they were recorded. Do not edit them to tidy them up.
- A comment that cites a file here uses the path `documentation/evidence/<file name>`.
- New evidence goes in this folder. Name it for what was measured and the date, for example `sky_band_sweep_vega_20260919.json`.
- Sphinx does not build this folder (see `exclude_patterns` in `documentation/conf.py`).

Run logs are not kept here. The app writes them to `${XDG_STATE_HOME:-~/.local/state}/astrometrics/logs`.
