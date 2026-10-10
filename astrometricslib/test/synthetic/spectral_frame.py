"""Purpose: Build a synthetic 2-D spectral frame whose truth is known.

Description: Tests of spectrum extraction and wavelength calibration need
an image where the right answer is known. This file draws a bright
zero-order star (the undispersed image of the target), a tilted trail of
light running away from it (the dispersed spectrum), and Gaussian
absorption lines (dips in the trail) at chosen wavelengths. It adds a
flat sky level and noise. It also returns the pixel column where each
line should appear, so a test can check that its measurement finds it.

Coordinate convention, chosen to match the extractor:

* The array has shape ``(ny, nx)``. The first index is the row (y), the
  second is the column (x). A pixel centre is at integer coordinates.
* The dispersion is horizontal (``orientation="horizontal"`` in
  ``SpectrumExtractor``). The trail runs from the zero order toward
  larger x. Wavelength grows with x.
* The trail centre at column ``x`` is
  ``y = y0 - tan(angle_deg) * (x - x0)``. This is the same rule as
  ``SpectrumExtractor.extract_with_flare_mask``, which uses
  ``slope = -tan(angle_degrees)``. So a positive angle moves the trail
  toward smaller row numbers as x grows.
* The offset ``d`` of a sample from the zero order is the column offset
  ``x - x0``, the same offset the pipeline passes to the wavelength
  calibration. It is not the distance along the tilted line.

Wavelength model: this generator uses a straight line,
``wavelength = d * dispersion_a_per_px``. The real instrument follows the
grating equation, ``wavelength = spacing * sin(arctan(x_mm / L))``, where
``x_mm`` is the offset on the sensor and ``L`` is the grating distance
(see ``optics_physics.calculate_wavelength``). A test that needs the real
relation should compute the line columns itself from the instrument's
parameters and pass the matching dispersion, or compare against the
columns returned here only where the straight-line model is accurate.
"""

from dataclasses import dataclass

import numpy as np

from astrometricslib.test.synthetic._pixel_math import FWHM_TO_SIGMA, pixel_fractions

# Full width at half maximum of every absorption line, in pixels.
LINE_FWHM_PX = 3.0
# The sensor clips at this value, in ADU.
SATURATION_ADU = 65535.0


@dataclass(frozen=True, eq=False)
class SyntheticSpectralFrame:
    """A synthetic spectral image together with its truth values.

    Attributes
    ----------
    image : `numpy.ndarray`
        The frame, shape ``(ny, nx)``, dtype float64, in ADU.
    zero_order_xy : `tuple` [`float`, `float`]
        Position ``(x, y)`` of the zero-order star centre, in pixels.
    angle_deg : `float`
        Tilt of the trail. The trail centre is at
        ``y = y0 - tan(angle_deg) * (x - x0)``.
    dispersion_a_per_px : `float`
        Dispersion in angstroms per pixel of column offset.
    trace_sigma_px : `float`
        Standard deviation of the trail cross-profile, in pixels, measured
        along a column. When the frame was made with
        ``trace_sigma_by_wavelength`` this is only the zero order's width;
        use `trace_sigma_at` for the trail's.
    line_wavelengths_a : `tuple` [`float`, ...]
        Wavelength of each injected line, in angstroms, in the order the
        lines were given.
    line_columns_px : `tuple` [`float`, ...]
        Column at which each line is centred: ``x0 + wavelength /
        dispersion_a_per_px``. Same order as ``line_wavelengths_a``.
    trace_sigma_by_wavelength : `tuple` or `None`
        The ``(wavelength_angstrom, sigma_px)`` entries the trail width was
        built from, or `None` when the width is the same at every column.
    """

    image: np.ndarray
    zero_order_xy: tuple[float, float]
    angle_deg: float
    dispersion_a_per_px: float
    trace_sigma_px: float
    line_wavelengths_a: tuple[float, ...]
    line_columns_px: tuple[float, ...]
    trace_sigma_by_wavelength: tuple[tuple[float, float], ...] | None = None

    def trace_sigma_at(self, wavelength_angstrom: np.ndarray | float) -> np.ndarray | float:
        """Return the true cross-profile width of the trail at a wavelength.

        Parameters
        ----------
        wavelength_angstrom : `numpy.ndarray` or `float`
            Wavelength or wavelengths, in angstroms.

        Returns
        -------
        sigma_px : `numpy.ndarray` or `float`
            The trail's standard deviation across the dispersion, in pixels.
            It is ``trace_sigma_px`` when the frame has no
            wavelength-dependent width.
        """
        if self.trace_sigma_by_wavelength is None:
            return self.trace_sigma_px
        nodes = np.array(sorted(self.trace_sigma_by_wavelength), dtype=np.float64)
        return np.interp(wavelength_angstrom, nodes[:, 0], nodes[:, 1])

    def trace_center_y(self, x: np.ndarray | float) -> np.ndarray | float:
        """Return the true row of the trail centre at column ``x``.

        Parameters
        ----------
        x : `numpy.ndarray` or `float`
            Column coordinate or coordinates.

        Returns
        -------
        y : `numpy.ndarray` or `float`
            Row of the trail centre, ``y0 - tan(angle) * (x - x0)``.
        """
        x0, y0 = self.zero_order_xy
        return y0 - np.tan(np.radians(self.angle_deg)) * (x - x0)


def _line_transmission(columns: np.ndarray, centre: float, depth: float) -> np.ndarray:
    """Return the fraction of light removed by one line in each column.

    The line is a Gaussian dip of full width `LINE_FWHM_PX`. The value for
    each column is the average of the dip over the column width, so a line
    that falls between two columns is shared between them.

    Parameters
    ----------
    columns : `numpy.ndarray`
        Integer column coordinates.
    centre : `float`
        Column of the line centre.
    depth : `float`
        Fractional depth of the line at its centre (0.5 removes half of
        the light at the centre of a column that is exactly on the line).

    Returns
    -------
    removed : `numpy.ndarray`
        Fraction of light removed in each column.
    """
    sigma = LINE_FWHM_PX * FWHM_TO_SIGMA
    # Fraction of a unit-area Gaussian in each column, rescaled so that
    # the centre value of the Gaussian is 1 (area = sigma * sqrt(2 pi)).
    area_fraction = pixel_fractions(centre, sigma, int(columns.max()) + 1)[:, 0]
    return depth * sigma * np.sqrt(2.0 * np.pi) * area_fraction[columns]


def make_spectral_frame(
    zero_order_xy: tuple[float, float] = (60.0, 128.0),
    angle_deg: float = 2.0,
    dispersion_a_per_px: float = 11.0,
    trail_length_px: int = 700,
    trace_sigma_px: float = 2.5,
    trace_sigma_by_wavelength: tuple[tuple[float, float], ...] | None = None,
    continuum_adu: float = 3000.0,
    lines: tuple[tuple[float, float], ...] = ((4861.0, 0.5), (6563.0, 0.6)),
    zero_order_flux_adu: float = 2e6,
    sky_adu: float = 150.0,
    read_noise_adu: float = 5.0,
    seed: int = 0,
    shape: tuple[int, int] = (256, 900),
    add_noise: bool = True,
) -> SyntheticSpectralFrame:
    """Make a synthetic spectral frame with known line positions.

    The steps are:

    1. Draw the zero-order star as a round Gaussian with standard
       deviation ``trace_sigma_px`` and total flux ``zero_order_flux_adu``.
    2. For each column ``x`` with ``0 < x - x0 <= trail_length_px``, set the
       total trail flux in that column to ``continuum_adu * (1 - sum of
       line dips)``. There is no trail at or left of the zero order.
    3. Spread that flux across rows with a Gaussian cross-profile
       (standard deviation ``trace_sigma_px``, or the width that
       ``trace_sigma_by_wavelength`` gives at that column's wavelength,
       integrated over each row) centred on
       ``y = y0 - tan(angle_deg) * (x - x0)``.
    4. Add the flat sky level.
    5. Unless ``add_noise`` is `False`, apply Poisson noise (gain fixed
       at 1 electron per ADU), then Gaussian read noise.
    6. Clip at 65535 ADU.

    The line at wavelength ``w`` (angstroms) is centred on the column
    ``x0 + w / dispersion_a_per_px``. See the module docstring for the
    coordinate convention and for why the wavelength model is a straight
    line.

    Parameters
    ----------
    zero_order_xy : `tuple` [`float`, `float`], optional
        Zero-order position ``(x, y)`` (default ``(60.0, 128.0)``).
    angle_deg : `float`, optional
        Trail tilt in degrees (default 2.0). Positive values move the
        trail toward smaller row numbers as x grows.
    dispersion_a_per_px : `float`, optional
        Angstroms per pixel of column offset (default 11.0).
    trail_length_px : `int`, optional
        Length of the trail in columns (default 700).
    trace_sigma_px : `float`, optional
        Standard deviation of the cross-profile in pixels (default 2.5).
    trace_sigma_by_wavelength : `tuple`, optional
        Makes the cross-profile width depend on wavelength, as it does in a
        spectrum whose red end is out of focus. Each entry is
        ``(wavelength_angstrom, sigma_px)``. The width at a column is the
        straight-line interpolation between the entries, held at the first
        or last entry outside them. The wavelength of a column follows the
        generator's straight-line model. The zero order keeps
        ``trace_sigma_px``. Default `None`: every column uses
        ``trace_sigma_px``, as before.
    continuum_adu : `float`, optional
        Total trail flux in one column, in ADU, outside any line (default
        3000.0). It is the sum over the rows of that column.
    lines : `tuple` [`tuple` [`float`, `float`], ...], optional
        Absorption lines as ``(wavelength_angstrom, fractional_depth)``.
        Each is a Gaussian dip of full width 3 pixels (default Hβ and Hα
        at 4861 and 6563 angstroms with depths 0.5 and 0.6).
    zero_order_flux_adu : `float`, optional
        Total flux of the zero-order star in ADU (default 2e6).
    sky_adu : `float`, optional
        Flat sky level in ADU per pixel (default 150.0).
    read_noise_adu : `float`, optional
        Standard deviation of the read noise in ADU (default 5.0).
    seed : `int`, optional
        Seed for ``numpy.random.default_rng`` (default 0). The Poisson
        draw comes first and the read-noise draw second.
    shape : `tuple` [`int`, `int`], optional
        Image shape ``(ny, nx)`` (default ``(256, 900)``).
    add_noise : `bool`, optional
        If `False`, return the clean image plus sky (default `True`).

    Returns
    -------
    frame : `SyntheticSpectralFrame`
        The image and its truth values.

    Raises
    ------
    ValueError
        If a line falls outside the trail or outside the image.
    """
    ny, nx = shape
    x0, y0 = zero_order_xy
    columns = np.arange(nx)
    column_offset = columns - x0

    line_columns = tuple(float(x0 + wavelength / dispersion_a_per_px) for wavelength, _ in lines)
    for wavelength, column in zip((w for w, _ in lines), line_columns, strict=True):
        if not (x0 < column <= min(x0 + trail_length_px, nx - 1)):
            raise ValueError(f"Line at {wavelength} A falls at column {column:.1f}, outside the trail.")

    on_trail = (column_offset > 0) & (column_offset <= trail_length_px)
    transmission = np.ones(nx)
    for (_, depth), column in zip(lines, line_columns, strict=True):
        transmission -= _line_transmission(columns, column, depth)
    column_flux = np.where(on_trail, continuum_adu * np.clip(transmission, 0.0, None), 0.0)

    centre_y = y0 - np.tan(np.radians(angle_deg)) * column_offset
    if trace_sigma_by_wavelength is None:
        column_sigma: np.ndarray | float = trace_sigma_px
    else:
        nodes = np.array(sorted(trace_sigma_by_wavelength), dtype=np.float64)
        column_sigma = np.interp(column_offset * dispersion_a_per_px, nodes[:, 0], nodes[:, 1])
    row_fractions = pixel_fractions(centre_y, column_sigma, ny)
    image = row_fractions * column_flux[np.newaxis, :]

    zero_columns = pixel_fractions(x0, trace_sigma_px, nx)[:, 0]
    zero_rows = pixel_fractions(y0, trace_sigma_px, ny)[:, 0]
    image += zero_order_flux_adu * np.outer(zero_rows, zero_columns)
    image += sky_adu

    if add_noise:
        rng = np.random.default_rng(seed)
        image = rng.poisson(np.clip(image, 0.0, None)).astype(np.float64)
        image += rng.normal(0.0, read_noise_adu, size=shape)
    image = np.minimum(image, SATURATION_ADU)

    return SyntheticSpectralFrame(
        image=image,
        zero_order_xy=(float(x0), float(y0)),
        angle_deg=float(angle_deg),
        dispersion_a_per_px=float(dispersion_a_per_px),
        trace_sigma_px=float(trace_sigma_px),
        line_wavelengths_a=tuple(float(w) for w, _ in lines),
        line_columns_px=line_columns,
        trace_sigma_by_wavelength=(
            None
            if trace_sigma_by_wavelength is None
            else tuple((float(w), float(sigma)) for w, sigma in trace_sigma_by_wavelength)
        ),
    )
