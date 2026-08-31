---
orphan: true
---

# Sample Data

Example FITS frames for following along with the tutorials and notebooks in
`documentation/notebooks/astrometrics/`, so the walkthroughs work without
first pointing a real telescope at the sky.

## Layout

One directory per target, named after the target's catalog ID. Frame role
and filter are encoded in the filename rather than a subfolder, matching
what the capture session (and `IMAGETYP`/`FILTER` FITS headers) already
produced:

```
sample_data/
└── M 13/
    ├── M_13_Light_Luminance_019.fits ... 023.fits   # 30s Luminance light subs
    ├── M_13_Light_Spectroscopy_024.fits ... 040.fits # 30s Spectroscopy light subs
    ├── M_13_Stacked_ZWO_ASI_533MM_Pro_405mm.fits     # stacked Luminance (1320s total)
    └── M_13_SPEC_Stacked_ZWO_ASI_533MM_Pro_405mm.fits # stacked Spectroscopy (1200s total)
```

`.fits`/`.fit` files here are tracked with Git LFS (see `.gitattributes`) —
run `git lfs install` once per clone before pulling.

## Using the sample data

The `user_guide/` notebooks (`01_working_with_targets.ipynb`,
`09_end_to_end_pipeline_M13.ipynb`, and others) register these files
automatically, through `scripts/sample_data_staging.py`'s
`stage_m13_sample_data()`. Use those rather than registering a file from
this directory directly — see the warning below.

To register a frame by hand instead, the ingestion script from
`documentation/notebooks/astrometrics/scripts/` takes any local path:

```bash
.venv/bin/python documentation/notebooks/astrometrics/scripts/local_image_ingestion.py \
  "M 13" "documentation/notebooks/astrometrics/sample_data/M 13/M_13_Light_Luminance_019.fits" \
  --role LIGHT
```

:::{warning}
Point that at a copy, not the file under this directory. Astrometrics
caches a solved WCS by writing it back into whichever FITS file it
solved — the right behavior for your own capture library, where it
saves re-solving the same frame on a later run, but not for this
directory's checked-in reference data: running astrometry or photometry
against a frame registered this way rewrites that file in place.
:::

## Provenance

| Target | Camera | Telescope | Filter | Exposure | Date | Notes |
|---|---|---|---|---|---|---|
| M 13 | ZWO ASI533MM Pro | Apertura 75Q (405mm, f/5.4) | Luminance | 30s × 5 subs | 2026-05-24 | Light frames `M_13_Light_Luminance_019-023.fits` |
| M 13 | ZWO ASI533MM Pro | Apertura 75Q (405mm, f/5.4) | Spectroscopy | 30s × 5 subs | 2026-05-24 | Light frames `M_13_Light_Spectroscopy_024/025/026/028/040.fits` |
| M 13 | ZWO ASI533MM Pro | Apertura 75Q (405mm, f/5.4) | Luminance | 1320s (stacked) | 2026-05-24 | `M_13_Stacked_ZWO_ASI_533MM_Pro_405mm.fits` |
| M 13 | ZWO ASI533MM Pro | Apertura 75Q (405mm, f/5.4) | Spectroscopy | 1200s (stacked) | 2026-05-24 | `M_13_SPEC_Stacked_ZWO_ASI_533MM_Pro_405mm.fits` |
