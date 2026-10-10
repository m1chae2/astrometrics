# Synthetic truth-known frames

These generators build artificial images where the right answer is known in
advance. A test can run a measurement on a generated frame and compare the
result with the truth that went in. They give a test a fixed target, so a
bug fix can prove that it recovers the truth within a stated tolerance.

All code is in this folder. Import from the package:

```python
from astrometricslib.test.synthetic import (
    SyntheticStar,
    make_photometry_frame,
    make_spectral_frame,
)
```

## Conventions shared by all generators

- **Array shape.** Images have shape `(ny, nx)`. The first index is the row
  (y). The second is the column (x), as in NumPy.
- **Pixel coordinates.** The centre of pixel `(row j, column i)` is at
  `(x, y) = (i, j)`. The pixel covers `i - 0.5` to `i + 0.5` in x. The same
  holds in y.
- **Units.** Fluxes and levels are in ADU (analog-to-digital units, the
  counts a camera reports). Positions and widths are in pixels.
- **Pixel integration.** A star or trail is a Gaussian. Each pixel holds the
  exact integral of the Gaussian over the pixel area, found with the error
  function. A pixel does not hold a point sample at its centre. The total
  of a noise-free star therefore equals its `flux_adu`.
- **Noise model.** The generator first builds the clean image plus the sky.
  It then multiplies by the gain (electrons per ADU), draws a Poisson number
  for each pixel (photon counting noise), and divides by the gain. Last, it
  adds Gaussian read noise. The result is clipped at the saturation level.
  Read noise can make faint pixels slightly negative.
- **Seed convention.** Every generator takes an integer `seed` and builds
  one `numpy.random.default_rng(seed)`. The Poisson draw comes first and the
  read-noise draw second. The same arguments and seed always return the same
  array. A different seed returns different noise.
- **Noise-free option.** `add_noise=False` skips the Poisson and read-noise
  steps. Use it to test a measurement's bias without noise in the way.
- **Return type.** Images are `float64` arrays.

## Photometry: `photometry_frame.py`

`SyntheticStar(x, y, flux_adu, fwhm_px=3.0)` describes one star.
`flux_adu` is the total integrated flux. `fwhm_px` is the full width at half
maximum, converted to a Gaussian standard deviation with
`sigma = fwhm / (2 sqrt(2 ln 2))`.

| Function | What it produces |
| --- | --- |
| `render_stars(stars, shape)` | A clean, sky-free image of the stars. |
| `make_photometry_frame(stars, shape, sky_adu, read_noise_adu, gain_e_per_adu, seed, saturation_adu, add_noise)` | One noisy frame: stars, flat sky, Poisson noise, read noise, clipping. |
| `make_photometry_fits(path, stars, *, date_obs, exptime_s, extra_header, **frame_kwargs)` | The same frame written to a FITS file with `DATE-OBS`, `EXPTIME`, and any extra cards. Returns the array it wrote. |
| `make_drifted_sequence(stars, n_frames, drift_px_per_frame, rotation_deg_per_frame, flux_scale, **frame_kwargs)` | A list of frames in which the field drifts, rotates, and changes brightness. |
| `drifted_stars(stars, frame_index, ...)` | The true star positions in one frame of that sequence. |
| `star_flux_multipliers(n_stars, frame_index, flux_scale)` | The flux multiplier of each star in one frame. |

### Sequence convention

Frame `k` starts at frame 0 (the input stars) and applies these rules:

1. **Rotation.** The field rotates about the frame centre
   `((nx - 1) / 2, (ny - 1) / 2)` by `k * rotation_deg_per_frame`. With x to
   the right and y increasing with the row number, a positive angle turns
   +x toward +y.
2. **Drift.** The field then shifts by `k * (dx, dy)`, where
   `(dx, dy) = drift_px_per_frame`. `dx` moves stars along columns. `dy`
   moves them along rows.
3. **Flux.** Each star's `flux_adu` is multiplied by a factor that depends
   on `flux_scale`:
   - `None`: the factor is 1.
   - A callable `f(k)`: every star gets `f(k)`. Use it for an airmass trend.
   - A dict `{star_index: f}`: the star at `star_index` in the input list
     gets `f(k)`. Every other star gets 1. Use it for a dip or a
     sinusoid on chosen stars.
4. **Noise.** Frame `k` uses the seed `seed + k`, so the frames have
   independent noise and the whole sequence repeats for a given `seed`.

## Spectroscopy: `spectral_frame.py`

`make_spectral_frame(...)` returns a `SyntheticSpectralFrame`. The frame
holds a bright zero-order star, a tilted trail of light running away from
it (the dispersed spectrum), and Gaussian absorption lines (dips in the
trail) at chosen wavelengths.

The result has these fields:

| Field | Meaning |
| --- | --- |
| `image` | The frame, shape `(ny, nx)`, in ADU. |
| `zero_order_xy` | The zero-order position `(x, y)` in pixels. |
| `angle_deg` | The tilt of the trail. |
| `dispersion_a_per_px` | Angstroms per pixel of column offset. |
| `trace_sigma_px` | Standard deviation of the trail cross-profile, in pixels. |
| `line_wavelengths_a` | Wavelength of each injected line, in angstroms. |
| `line_columns_px` | The column where each line is centred. |

`trace_center_y(x)` returns the true row of the trail centre at column `x`.

### Geometry and convention

The generator matches `SpectrumExtractor` for horizontal dispersion.

- The trail runs from the zero order toward larger x. Wavelength grows with x.
- The trail centre is at `y = y0 - tan(angle_deg) * (x - x0)`. The extractor
  uses the same rule (`slope = -tan(angle_degrees)`). A positive angle moves
  the trail toward smaller row numbers as x grows.
- The offset `d` of a sample from the zero order is the column offset
  `x - x0`, not the distance along the tilted line.
- There is no trail at or left of the zero order. The trail ends at
  `x0 + trail_length_px`.
- The cross-profile is a Gaussian along each column, with standard deviation
  `trace_sigma_px`. The flux summed down one column outside any line equals
  `continuum_adu`.
- Each line is a Gaussian dip with a full width at half maximum of 3 pixels.
  `fractional_depth` is the fraction of the light removed at the line centre.
  A line that falls between two columns shares its depth between them, so
  the deepest column can be slightly shallower than the requested depth.
- The zero order is a round Gaussian with the same standard deviation as the
  trail and a total flux of `zero_order_flux_adu`.
- The gain is fixed at 1 electron per ADU. The frame clips at 65535 ADU.

### Wavelength model

The generator uses a straight line: `wavelength = d * dispersion_a_per_px`,
so a line at wavelength `w` is centred on column `x0 + w / dispersion_a_per_px`.

The real instrument follows the grating equation,
`wavelength = spacing * sin(arctan(x_mm / L))`. Here `x_mm` is the offset on
the sensor, `L` is the distance from the grating to the sensor, and `spacing`
is the distance between grating lines. See
`pipelines/spectroscopy/pre_processing/optics_physics.py`. A test that needs
the real relation must compute its own line columns from the instrument
parameters and not use `line_columns_px`. The straight-line model is a good
approximation only near the zero order.

## Extending the generators

- Add new frame types as a new module here and export the names in
  `__init__.py`.
- Keep the conventions above. Document any new quantity with its units.
- Give each generator an `add_noise` switch and a `seed`.
- Add a test to `test_synthetic_generators.py` that recovers the injected
  truth.

## Tests

`test_synthetic_generators.py` checks flux conservation, sub-pixel centroid
shifts, the drift rule, line depth and column, the extractor's tilt sign,
and seed repeatability. Run it with:

```bash
.venv/bin/pytest astrometricslib/test/synthetic -q
```

For exact behaviour, read the code.
