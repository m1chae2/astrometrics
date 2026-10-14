r"""Find the best focuser position for a slitless spectrum from a focus sweep.

A grating in a converging beam does not put the first-order spectrum in a flat
plane. If the camera is focused on the zero order (the star's undispersed
image), the red end of the spectrum can sit out of focus. The focuser position
that is best for the blue is then not the best for the red. This script
measures how the focus of each part of the spectrum changes with the focuser
position, so the observer can pick the position that suits the whole spectrum.

Use it at the telescope:

1. Point at one bright, unsaturated star with the grating in place.
2. Take one spectral frame at each of five or more focuser positions, spaced
   evenly across the focus range. The positions must straddle the best focus,
   so some frames are slightly inside it and some slightly outside it.
   Each frame must carry the ``FOCUSPOS`` header card (the focuser position).
3. Run:

       python -m astrometricslib.scripts.spectral_focus_sweep \
           frame_1.fits frame_2.fits ...

   Pass ``--star-position X Y`` (the zero-order pixel position) if the star is
   not the source nearest the frame centre.

What it does:

1. It extracts the star's spectrum from each frame with the normal spectroscopy
   pipeline extractor. Nothing is saved to the catalog.
2. It measures the trail's width across the dispersion in 400 A bands from
   4200 to 8000 A (`measure_line_spread`). The width is a full width at half
   maximum (FWHM) in pixels. For a spectrum limited by seeing and focus the
   star's image is round, so this width is also the blur along the spectrum.
3. For each band, it fits a parabola to FWHM against focuser position. The
   lowest point of the parabola is that band's best focus.
4. It does the same for three probe wavelengths (4500, 5500 and 6500 A), using
   each frame's profile read at that wavelength. It reports the focuser
   position that gives the smallest FWHM at 5500 A (mid-spectrum) and the FWHM
   that position gives at 4500 and 6500 A.

Limits of the method:

* A parabola in FWHM is an approximation. The true curve of a defocused star is
  closer to a hyperbola, so the fit is best near the minimum. Use positions
  that bracket the best focus closely.
* Every band needs a minimum on the parabola (the curve opens upward)
  inside the range of positions you took. The script says so when it does
  not. Then extend the sweep in the direction the FWHM is still falling.
* Seeing changes from frame to frame. Take the frames close together in time.
* A band with fewer than `MINIMUM_FOCUS_POSITIONS` working frames is not
  fitted.

For exact behavior, read the code.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.measured_line_spread import MeasuredLineSpread
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.measured_line_spread import measure_line_spread
from astrometricslib.pipelines.spectroscopy.pre_processing.standard_star_selection import (
    select_standard_star,
)

# The fewest different focuser positions a parabola needs. Three points fix
# a parabola exactly, with no check on it; five or more let the fit average
# out seeing noise. The script refuses fewer than three and tells the observer
# to take at least five.
MINIMUM_FOCUS_POSITIONS = 3
RECOMMENDED_FOCUS_POSITIONS = 5

# The wavelength whose best focus the observer is told to use, in Angstroms.
# Mid-spectrum, so the blue and the red end up about equally far from focus.
FOCUS_TARGET_ANGSTROM = 5500.0

# The other wavelengths reported at the chosen position, in Angstroms.
CHECK_WAVELENGTHS_ANGSTROM = (4500.0, 6500.0)


@dataclass(frozen=True)
class FocusFit:
    """A parabola fitted to trail FWHM against focuser position.

    Attributes
    ----------
    coefficients : `tuple` [`float`, `float`, `float`]
        The parabola ``a * u**2 + b * u + c``, where ``u`` is the focuser
        position minus `position_offset`.
    position_offset : `float`
        The mean focuser position of the sweep. The fit is done around it so
        large position numbers do not spoil the arithmetic.
    best_position : `float`
        The focuser position of the parabola's lowest point.
    minimum_fwhm_px : `float`
        The parabola's value at `best_position`, in pixels.
    is_inside_sweep : `bool`
        Whether `best_position` lies between the lowest and highest
        focuser position of the sweep. If not, the minimum is an extrapolation.
    """

    coefficients: tuple[float, float, float]
    position_offset: float
    best_position: float
    minimum_fwhm_px: float
    is_inside_sweep: bool

    def fwhm_at(self, position: float) -> float:
        """Give the fitted FWHM at a focuser position.

        Parameters
        ----------
        position : `float`
            The focuser position.

        Returns
        -------
        fwhm_px : `float`
            The parabola's value there, in pixels.
        """
        return float(np.polyval(self.coefficients, position - self.position_offset))


@dataclass(frozen=True)
class FrameProfile:
    """The line-spread profile measured on one frame of the sweep.

    Attributes
    ----------
    path : `str`
        The frame's file.
    focuser_position : `float`
        The ``FOCUSPOS`` header value.
    profile : `MeasuredLineSpread`
        The frame's trail FWHM against wavelength.
    """

    path: str
    focuser_position: float
    profile: MeasuredLineSpread


@dataclass(frozen=True)
class SweepResult:
    """The result of analysing a whole focus sweep.

    Attributes
    ----------
    band_fits : `dict` [`float`, `FocusFit` or `None`]
        The fit for each band centre, in Angstroms. `None` for a band that
        could not be fitted.
    probe_fits : `dict` [`float`, `FocusFit` or `None`]
        The fit for each probe wavelength, in Angstroms (the target and the
        check wavelengths).
    recommended_position : `float` or `None`
        The focuser position that minimizes the FWHM at the target
        wavelength, or `None` when that fit failed.
    check_fwhm_px : `dict` [`float`, `float`]
        The FWHM, in pixels, the recommended position gives at each check
        wavelength. Empty when there is no recommended position.
    """

    band_fits: dict[float, FocusFit | None]
    probe_fits: dict[float, FocusFit | None]
    recommended_position: float | None
    check_fwhm_px: dict[float, float]


def fit_focus_curve(
    positions: np.ndarray | list[float], fwhm_px: np.ndarray | list[float]
) -> FocusFit | None:
    """Fit a parabola of FWHM against focuser position and find its minimum.

    Parameters
    ----------
    positions : `numpy.ndarray` or `list` [`float`]
        The focuser position of each frame.
    fwhm_px : `numpy.ndarray` or `list` [`float`]
        The FWHM measured on each frame, in pixels. NaN marks a frame with no
        measurement; it is left out.

    Returns
    -------
    fit : `FocusFit` or `None`
        The fit, or `None` when fewer than `MINIMUM_FOCUS_POSITIONS` different
        positions have a measurement or the parabola does not open upward
        (it has no minimum).
    """
    x = np.asarray(positions, dtype=float)
    y = np.asarray(fwhm_px, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if np.unique(x).size < MINIMUM_FOCUS_POSITIONS:
        return None
    offset = float(np.mean(x))
    a, b, c = (float(value) for value in np.polyfit(x - offset, y, 2))
    if a <= 0:
        return None
    vertex = -b / (2.0 * a)
    return FocusFit(
        coefficients=(a, b, c),
        position_offset=offset,
        best_position=vertex + offset,
        minimum_fwhm_px=c - b * b / (4.0 * a),
        is_inside_sweep=bool(x.min() <= vertex + offset <= x.max()),
    )


def analyse_sweep(frames: list[FrameProfile]) -> SweepResult:
    """Fit the focus curves of a sweep and choose the focuser position.

    Parameters
    ----------
    frames : `list` [`FrameProfile`]
        The measured profile of every frame.

    Returns
    -------
    result : `SweepResult`
        The per-band fits, the probe fits and the recommended position.
    """
    positions = np.array([frame.focuser_position for frame in frames], dtype=float)

    # A band is fitted only from the frames that have it.
    all_centres = sorted({centre for frame in frames for centre in frame.profile.wavelength_angstrom})
    band_fits: dict[float, FocusFit | None] = {}
    for centre in all_centres:
        values = np.full(len(frames), np.nan)
        for index, frame in enumerate(frames):
            if centre in frame.profile.wavelength_angstrom:
                values[index] = frame.profile.fwhm_px[frame.profile.wavelength_angstrom.index(centre)]
        band_fits[centre] = fit_focus_curve(positions, values)

    probe_fits: dict[float, FocusFit | None] = {}
    for wavelength in (FOCUS_TARGET_ANGSTROM, *CHECK_WAVELENGTHS_ANGSTROM):
        probe_values = np.array([
            float(np.interp(wavelength, frame.profile.wavelength_angstrom, frame.profile.fwhm_px))
            if frame.profile.wavelength_angstrom[0] <= wavelength <= frame.profile.wavelength_angstrom[-1]
            else np.nan
            for frame in frames
        ])
        probe_fits[wavelength] = fit_focus_curve(positions, probe_values)

    target = probe_fits[FOCUS_TARGET_ANGSTROM]
    recommended = target.best_position if target is not None else None
    check = {}
    if recommended is not None:
        for wavelength in CHECK_WAVELENGTHS_ANGSTROM:
            fit = probe_fits[wavelength]
            if fit is not None:
                check[wavelength] = fit.fwhm_at(recommended)
    return SweepResult(band_fits, probe_fits, recommended, check)


def read_focuser_position(path: str) -> float:
    """Read the ``FOCUSPOS`` card of a frame.

    Parameters
    ----------
    path : `str`
        The frame's FITS file.

    Returns
    -------
    position : `float`
        The focuser position.

    Raises
    ------
    ProcessingError
        If the frame has no usable ``FOCUSPOS`` card.
    """
    value = AstrometricsImage(path).header.get("FOCUSPOS")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ProcessingError(f"{path} has no usable FOCUSPOS header card (found {value!r}).") from error


def measure_frame(
    path: str, pipeline: SpectroscopyPipeline, star_position: tuple[float, float] | None = None
) -> FrameProfile:
    """Extract the star's spectrum from one frame and measure its trail FWHM.

    Parameters
    ----------
    path : `str`
        The frame's FITS file.
    pipeline : `SpectroscopyPipeline`
        The pipeline whose extractor reads the frame.
    star_position : `tuple` [`float`, `float`], optional
        The star's zero-order ``(x, y)`` pixel position. By default the
        pipeline's source detection runs and the source nearest the frame
        centre is used.

    Returns
    -------
    frame : `FrameProfile`
        The frame's focuser position and measured profile.

    Raises
    ------
    ProcessingError
        If the frame has no focuser position, no source is found, nothing
        can be extracted, or the trail width gives no profile.
    """
    focuser_position = read_focuser_position(path)
    image = AstrometricsImage(path)
    if star_position is None:
        # Imported here: detection is only needed when no position is given.
        from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline

        context = AstrometryPipeline().process(path, attempt_plate_solving=False)
        star = select_standard_star(context.stellar_objects, context.image.data.shape)
        star_position = (
            float(star.star_data.get("x_centroid", star.star_data.get("xcentroid"))),
            float(star.star_data.get("y_centroid", star.star_data.get("ycentroid"))),
        )
    results = pipeline.process_image(image, target_stars=[star_position])
    if not results:
        raise ProcessingError(f"No spectrum could be extracted from {path} at {star_position}.")
    extraction = results[0]
    profile = measure_line_spread(
        [float(wavelength) * 10.0 for wavelength in extraction["wavelengths"]],
        extraction.get("trail_width_px"),
        extraction.get("sample_distances_px"),
    )
    if profile is None:
        raise ProcessingError(f"The trail width of {path} gave no line-spread profile (star too faint?).")
    return FrameProfile(path=str(path), focuser_position=focuser_position, profile=profile)


def format_report(frames: list[FrameProfile], result: SweepResult) -> str:
    """Write the sweep's table and recommendation as text.

    Parameters
    ----------
    frames : `list` [`FrameProfile`]
        The measured frames.
    result : `SweepResult`
        The analysis of the sweep.

    Returns
    -------
    report : `str`
        The report, ready to print.
    """
    lines = ["Frames:"]
    for frame in sorted(frames, key=lambda item: item.focuser_position):
        lines.append(f"  FOCUSPOS {frame.focuser_position:>9.1f}  {Path(frame.path).name}")
    lines += [
        "",
        "Best focus by wavelength band (parabola fitted to trail FWHM against focuser position):",
        f"  {'band centre (A)':>16}  {'best FOCUSPOS':>14}  {'FWHM there (px)':>16}  note",
    ]
    for centre, fit in result.band_fits.items():
        if fit is None:
            lines.append(f"  {centre:>16.0f}  {'-':>14}  {'-':>16}  no minimum in the data")
        else:
            note = "" if fit.is_inside_sweep else "outside the sweep: extend it"
            lines.append(
                f"  {centre:>16.0f}  {fit.best_position:>14.1f}  {fit.minimum_fwhm_px:>16.2f}  {note}"
            )
    lines.append("")
    if result.recommended_position is None:
        lines.append(
            f"No best focus at {FOCUS_TARGET_ANGSTROM:.0f} A: the fitted curve has no minimum. "
            f"Take at least {RECOMMENDED_FOCUS_POSITIONS} frames that straddle the best focus."
        )
        return "\n".join(lines)
    target = result.probe_fits[FOCUS_TARGET_ANGSTROM]
    lines.append(
        f"Focus position that minimizes the FWHM at {FOCUS_TARGET_ANGSTROM:.0f} A: "
        f"{result.recommended_position:.1f} (FWHM {target.minimum_fwhm_px:.2f} px)"
    )
    if not target.is_inside_sweep:
        lines.append("  This is outside the positions you sampled. Extend the sweep and run it again.")
    for wavelength in CHECK_WAVELENGTHS_ANGSTROM:
        if wavelength in result.check_fwhm_px:
            fit = result.probe_fits[wavelength]
            lines.append(
                f"  At that position the FWHM at {wavelength:.0f} A is "
                f"{result.check_fwhm_px[wavelength]:.2f} px "
                f"(best possible there {fit.minimum_fwhm_px:.2f} px at FOCUSPOS {fit.best_position:.1f})"
            )
        else:
            lines.append(f"  No fit at {wavelength:.0f} A.")
    if len(frames) < RECOMMENDED_FOCUS_POSITIONS:
        lines.append(
            f"Only {len(frames)} frames: take at least {RECOMMENDED_FOCUS_POSITIONS} for a trustworthy fit."
        )
    return "\n".join(lines)


def run_focus_sweep(
    paths: list[str],
    pipeline: SpectroscopyPipeline,
    star_position: tuple[float, float] | None = None,
) -> tuple[list[FrameProfile], SweepResult]:
    """Measure every frame and analyse the sweep.

    Parameters
    ----------
    paths : `list` [`str`]
        The frames, each with a ``FOCUSPOS`` card.
    pipeline : `SpectroscopyPipeline`
        The pipeline whose extractor reads the frames.
    star_position : `tuple` [`float`, `float`], optional
        The star's zero-order pixel position, when it is not the source
        nearest the frame centre.

    Returns
    -------
    frames : `list` [`FrameProfile`]
        The measured frames.
    result : `SweepResult`
        The analysis.

    Raises
    ------
    ProcessingError
        If fewer than `MINIMUM_FOCUS_POSITIONS` frames are given or a frame
        cannot be measured.
    """
    if len(paths) < MINIMUM_FOCUS_POSITIONS:
        raise ProcessingError(
            f"A focus sweep needs at least {MINIMUM_FOCUS_POSITIONS} frames; got {len(paths)}."
        )
    frames = [measure_frame(path, pipeline, star_position) for path in paths]
    return frames, analyse_sweep(frames)


def run_script(argv: list[str] | None = None) -> int:
    """Run the focus sweep from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        0 when a best focus was found, 1 when a frame could not be measured
        or the fit has no minimum.
    """
    parser = argparse.ArgumentParser(
        description="Find the best focuser position from a spectral focus sweep."
    )
    parser.add_argument("frames", nargs="+", help="Spectral frames of one star at different FOCUSPOS values.")
    parser.add_argument(
        "--star-position",
        type=float,
        nargs=2,
        metavar=("X", "Y"),
        default=None,
        help="The star's zero-order pixel position. By default the source nearest the frame centre is used.",
    )
    arguments = parser.parse_args(argv)
    try:
        frames, result = run_focus_sweep(
            arguments.frames,
            SpectroscopyPipeline(),
            tuple(arguments.star_position) if arguments.star_position else None,
        )
    except ProcessingError as error:
        print(error)
        return 1
    print(format_report(frames, result))
    return 0 if result.recommended_position is not None else 1


if __name__ == "__main__":
    sys.exit(run_script())
