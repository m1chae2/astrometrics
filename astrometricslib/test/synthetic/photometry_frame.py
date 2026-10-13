"""Purpose: Build synthetic star-field frames whose true flux is known.

Description: Tests of photometry (measuring how bright a star is) need
images where the right answer is known in advance. This file renders
Gaussian stars of a chosen total flux at chosen sub-pixel positions, adds
a flat sky level, and applies a realistic noise model (Poisson noise from
photon counting, then Gaussian read noise). It can also write a frame to a
FITS file and build a sequence of frames in which the star field drifts,
rotates, or changes brightness from frame to frame.

Conventions used by every function here:

* Arrays have shape ``(ny, nx)``. The first index is the row (y), the
  second is the column (x), as in NumPy.
* Pixel ``(row j, column i)`` covers x from ``i - 0.5`` to ``i + 0.5`` and
  y from ``j - 0.5`` to ``j + 0.5``. The centre of the pixel is at the
  integer pair ``(i, j)``. So the centre of the first pixel is (0, 0).
* All fluxes and levels are in ADU (analog-to-digital units, the counts
  the camera reports). "Gain" converts electrons to ADU.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.test.synthetic._pixel_math import FWHM_TO_SIGMA, pixel_fractions

FluxScale = Callable[[int], float] | Mapping[int, Callable[[int], float]] | None


@dataclass(frozen=True)
class SyntheticStar:
    """One artificial star, described by its true properties.

    Attributes
    ----------
    x : `float`
        Column of the star centre, in pixels. Can have a fractional part.
    y : `float`
        Row of the star centre, in pixels. Can have a fractional part.
    flux_adu : `float`
        Total integrated flux of the star, in ADU. This is the sum over
        every pixel of the star's light, with no sky.
    fwhm_px : `float`
        Full width at half maximum of the star, in pixels. The star is a
        round Gaussian, so its standard deviation is
        ``fwhm_px / (2 * sqrt(2 * ln 2))``.
    """

    x: float
    y: float
    flux_adu: float
    fwhm_px: float = 3.0


def render_stars(stars: list[SyntheticStar], shape: tuple[int, int] = (256, 256)) -> np.ndarray:
    """Render stars onto a noise-free, sky-free image.

    Each star is a 2-D Gaussian. The value of each pixel is the exact
    integral of the Gaussian over the pixel area, found with the error
    function. It is not a sample at the pixel centre. This makes the sum
    of all pixels equal to the star's ``flux_adu`` when the star lies well
    inside the frame.

    Parameters
    ----------
    stars : `list` [`SyntheticStar`]
        The stars to draw.
    shape : `tuple` [`int`, `int`], optional
        Image shape ``(ny, nx)`` (default ``(256, 256)``).

    Returns
    -------
    image : `numpy.ndarray`
        Array of shape ``shape``, dtype float64, in ADU.
    """
    ny, nx = shape
    image = np.zeros(shape, dtype=np.float64)
    for star in stars:
        sigma = star.fwhm_px * FWHM_TO_SIGMA
        column_fraction = pixel_fractions(star.x, sigma, nx)[:, 0]
        row_fraction = pixel_fractions(star.y, sigma, ny)[:, 0]
        image += star.flux_adu * np.outer(row_fraction, column_fraction)
    return image


def make_photometry_frame(
    stars: list[SyntheticStar],
    shape: tuple[int, int] = (256, 256),
    sky_adu: float = 200.0,
    read_noise_adu: float = 5.0,
    gain_e_per_adu: float = 1.0,
    seed: int = 0,
    saturation_adu: float = 65535.0,
    add_noise: bool = True,
) -> np.ndarray:
    """Make one synthetic star-field frame with a known truth.

    The steps are:

    1. Render each star as a pixel-integrated 2-D Gaussian (see
       `render_stars`).
    2. Add the flat sky level to every pixel.
    3. Convert to electrons (multiply by ``gain_e_per_adu``), draw a
       Poisson random number for each pixel (photon counting noise), and
       convert back to ADU.
    4. Add Gaussian read noise with standard deviation ``read_noise_adu``.
    5. Clip the result at ``saturation_adu``.

    Parameters
    ----------
    stars : `list` [`SyntheticStar`]
        The stars to draw.
    shape : `tuple` [`int`, `int`], optional
        Image shape ``(ny, nx)`` (default ``(256, 256)``).
    sky_adu : `float`, optional
        Flat sky level in ADU per pixel (default 200.0).
    read_noise_adu : `float`, optional
        Standard deviation of the read noise, in ADU (default 5.0).
    gain_e_per_adu : `float`, optional
        Electrons per ADU (default 1.0). A larger gain means each ADU
        stands for more electrons, so the Poisson noise is smaller in ADU.
    seed : `int`, optional
        Seed for ``numpy.random.default_rng`` (default 0). The same seed
        and arguments always give the same array. The Poisson draw comes
        first and the read-noise draw second.
    saturation_adu : `float`, optional
        Value at which the image is clipped from above (default 65535.0).
    add_noise : `bool`, optional
        If `False`, skip steps 3 and 4 and return the clean image (plus
        sky, clipped). Use this to test a measurement without noise
        (default `True`).

    Returns
    -------
    image : `numpy.ndarray`
        Array of shape ``shape``, dtype float64, in ADU. Read noise can
        make faint pixels slightly negative.
    """
    clean = render_stars(stars, shape) + sky_adu
    if not add_noise:
        return np.minimum(clean, saturation_adu)
    rng = np.random.default_rng(seed)
    electrons = np.clip(clean * gain_e_per_adu, 0.0, None)
    noisy = rng.poisson(electrons).astype(np.float64) / gain_e_per_adu
    noisy += rng.normal(0.0, read_noise_adu, size=shape)
    return np.minimum(noisy, saturation_adu)


def make_photometry_fits(
    path: str | Path,
    stars: list[SyntheticStar],
    *,
    date_obs: str,
    exptime_s: float,
    extra_header: dict | None = None,
    **frame_kwargs: float | tuple[int, int] | bool,
) -> np.ndarray:
    """Write a synthetic star-field frame to a FITS file.

    The file holds one image (float64) and a header with ``DATE-OBS``,
    ``EXPTIME``, and any cards in ``extra_header``. The file overwrites
    an existing file of the same name.

    Parameters
    ----------
    path : `str` or `pathlib.Path`
        Where to write the file.
    stars : `list` [`SyntheticStar`]
        The stars to draw.
    date_obs : `str`
        Value of the ``DATE-OBS`` card, an ISO time such as
        ``"2026-05-01T00:00:00"``.
    exptime_s : `float`
        Value of the ``EXPTIME`` card, the exposure time in seconds.
    extra_header : `dict`, optional
        More header cards, as ``{keyword: value}``. These are written
        after ``DATE-OBS`` and ``EXPTIME`` and can replace them.
    **frame_kwargs
        Passed to `make_photometry_frame` (``shape``, ``sky_adu``,
        ``read_noise_adu``, ``gain_e_per_adu``, ``seed``,
        ``saturation_adu``, ``add_noise``).

    Returns
    -------
    image : `numpy.ndarray`
        The array that was written, identical to what reading the file
        back gives.
    """
    image = make_photometry_frame(stars, **frame_kwargs)
    hdu = fits.PrimaryHDU(image)
    hdu.header["DATE-OBS"] = date_obs
    hdu.header["EXPTIME"] = exptime_s
    for keyword, value in (extra_header or {}).items():
        hdu.header[keyword] = value
    hdu.writeto(Path(path), overwrite=True)
    return image


def drifted_stars(
    stars: list[SyntheticStar],
    frame_index: int,
    drift_px_per_frame: tuple[float, float] = (0.3, -0.2),
    rotation_deg_per_frame: float = 0.0,
    shape: tuple[int, int] = (256, 256),
) -> list[SyntheticStar]:
    """Return the true star positions in one frame of a drifting sequence.

    Frame ``k`` is built from the input positions ``p0 = (x0, y0)`` as::

        p_k = c + R(k * rotation_deg_per_frame) @ (p0 - c)
              + k * (dx, dy)

    where ``c = ((nx - 1) / 2, (ny - 1) / 2)`` is the frame centre,
    ``(dx, dy) = drift_px_per_frame``, and ``R(a)`` is the rotation by
    angle ``a`` about the origin of an (x, y) plane with x to the right
    and y increasing with the row number. A positive angle turns the
    direction +x toward +y. The rotation acts first and the drift is
    added after it. Frame 0 is the input, unchanged.

    Parameters
    ----------
    stars : `list` [`SyntheticStar`]
        Star positions in frame 0.
    frame_index : `int`
        Index ``k`` of the frame.
    drift_px_per_frame : `tuple` [`float`, `float`], optional
        Shift ``(dx, dy)`` in pixels added for each frame (default
        ``(0.3, -0.2)``).
    rotation_deg_per_frame : `float`, optional
        Rotation about the frame centre per frame, in degrees (default
        0.0).
    shape : `tuple` [`int`, `int`], optional
        Image shape ``(ny, nx)``, used to find the frame centre.

    Returns
    -------
    moved : `list` [`SyntheticStar`]
        New stars with updated ``x`` and ``y``. Flux and FWHM are copied.
    """
    ny, nx = shape
    centre_x = (nx - 1) / 2.0
    centre_y = (ny - 1) / 2.0
    angle = np.radians(frame_index * rotation_deg_per_frame)
    cos_a, sin_a = float(np.cos(angle)), float(np.sin(angle))
    moved = []
    for star in stars:
        rel_x = star.x - centre_x
        rel_y = star.y - centre_y
        new_x = centre_x + cos_a * rel_x - sin_a * rel_y + frame_index * drift_px_per_frame[0]
        new_y = centre_y + sin_a * rel_x + cos_a * rel_y + frame_index * drift_px_per_frame[1]
        moved.append(replace(star, x=new_x, y=new_y))
    return moved


def star_flux_multipliers(n_stars: int, frame_index: int, flux_scale: FluxScale) -> list[float]:
    """Return the flux multiplier of each star for one frame.

    Parameters
    ----------
    n_stars : `int`
        Number of stars.
    frame_index : `int`
        Index of the frame.
    flux_scale : `callable` or `dict` or `None`
        ``None`` gives 1.0 for every star. A callable ``f(frame_index)``
        gives the same multiplier ``f(frame_index)`` to every star (an
        airmass trend). A dict ``{star_index: f}`` applies
        ``f(frame_index)`` to the star at that index in the input list
        and 1.0 to every star not in the dict (a dip or a sinusoid on
        chosen stars).

    Returns
    -------
    multipliers : `list` [`float`]
        One multiplier per star.
    """
    if flux_scale is None:
        return [1.0] * n_stars
    if callable(flux_scale):
        return [float(flux_scale(frame_index))] * n_stars
    return [float(flux_scale[i](frame_index)) if i in flux_scale else 1.0 for i in range(n_stars)]


def make_drifted_sequence(
    stars: list[SyntheticStar],
    n_frames: int,
    drift_px_per_frame: tuple[float, float] = (0.3, -0.2),
    rotation_deg_per_frame: float = 0.0,
    flux_scale: FluxScale = None,
    **frame_kwargs: float | tuple[int, int] | bool,
) -> list[np.ndarray]:
    """Make a sequence of frames with drift, rotation, and flux changes.

    Frame ``k`` (``k = 0 ... n_frames - 1``) uses these rules:

    * Positions come from `drifted_stars`: rotate about the frame centre
      by ``k * rotation_deg_per_frame``, then add ``k * drift_px_per_frame``
      (as ``(dx, dy)``, so ``dx`` moves along columns and ``dy`` along
      rows).
    * Each star's ``flux_adu`` is multiplied by the multiplier from
      `star_flux_multipliers`. ``flux_scale`` is either `None` (no
      change), a callable ``f(k)`` applied to every star, or a dict
      ``{star_index: f}`` where ``f(k)`` applies only to the star at that
      index in ``stars``.
    * The frame uses the seed ``seed + k``, where ``seed`` is the value
      in ``frame_kwargs`` (default 0). So every frame has independent
      noise, and the whole sequence repeats exactly for a given ``seed``.

    Parameters
    ----------
    stars : `list` [`SyntheticStar`]
        Star positions and fluxes in frame 0.
    n_frames : `int`
        Number of frames to make.
    drift_px_per_frame : `tuple` [`float`, `float`], optional
        Shift ``(dx, dy)`` in pixels per frame (default ``(0.3, -0.2)``).
    rotation_deg_per_frame : `float`, optional
        Rotation about the frame centre per frame, in degrees (default
        0.0).
    flux_scale : `callable` or `dict`, optional
        Flux multiplier rule described above (default `None`).
    **frame_kwargs
        Passed to `make_photometry_frame` (``shape``, ``sky_adu``,
        ``read_noise_adu``, ``gain_e_per_adu``, ``seed``,
        ``saturation_adu``, ``add_noise``).

    Returns
    -------
    frames : `list` [`numpy.ndarray`]
        ``n_frames`` arrays, each as returned by `make_photometry_frame`.
    """
    shape = frame_kwargs.get("shape", (256, 256))
    base_seed = int(frame_kwargs.pop("seed", 0))
    frames = []
    for k in range(n_frames):
        moved = drifted_stars(stars, k, drift_px_per_frame, rotation_deg_per_frame, shape)
        multipliers = star_flux_multipliers(len(stars), k, flux_scale)
        scaled = [replace(s, flux_adu=s.flux_adu * m) for s, m in zip(moved, multipliers, strict=True)]
        frames.append(make_photometry_frame(scaled, seed=base_seed + k, **frame_kwargs))
    return frames
