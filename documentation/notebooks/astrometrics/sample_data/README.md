# Sample Data

Example FITS frames for following along with the tutorials and notebooks in
`documentation/notebooks/astrometrics/`, so the walkthroughs work without
first pointing a real telescope at the sky.

## Layout

One directory per target, named after the target's catalog ID, with frames
grouped by role (`lights/`, `darks/`, `flats/`, `biases/`):

```
sample_data/
└── M 13/
    ├── lights/
    │   └── M_13_Light_001.fits
    ├── darks/
    ├── flats/
    └── biases/
```

`.fits`/`.fit` files here are tracked with Git LFS (see `.gitattributes`) —
run `git lfs install` once per clone before pulling.

## Using the sample data

Register a frame under a target with the ingestion script from
`documentation/notebooks/astrometrics/scripts/`:

```bash
.venv/bin/python documentation/notebooks/astrometrics/scripts/local_image_ingestion.py \
  "M 13" documentation/notebooks/astrometrics/sample_data/M\ 13/lights/M_13_Light_001.fits \
  --role LIGHT
```

Repeat for each dark/flat/bias frame you want indexed, then continue with
the `user_guide/` notebooks (e.g. `03_stacking_and_calibration.ipynb`).

## Provenance

Document the source of each set of frames here as they're added — telescope,
camera, filter, exposure length, and date — so the data's calibration
history stays traceable.

| Target | Camera | Telescope | Filter | Exposure | Notes |
|---|---|---|---|---|---|
| _(none yet)_ | | | | | |
