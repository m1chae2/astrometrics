"""Tuner for the pointing error limits of the centre-star naming.

A spectral stack has no plate solution of its own, so the star at its centre
is named from the mount's reported position (the "hint"). The mount points
only so well, so three limits are set from how far the report is from the
truth (register entries AST-09 and AST-D14):

- `POINTING_ERROR_SEARCH_RADIUS_ARCSEC`: how far from the hint the target may
  really be.
- `CENTRE_STAR_SEARCH_RADIUS_FRACTION`: the same distance seen in the image,
  as a fraction of the frame's shorter side, with a margin for the target
  being re-centered or drifting.
- `HINT_MATCH_WARNING_ARCSEC`: the distance beyond which a match of the
  nearest catalog star to the hint is reported as a probable mislabel.

The truth comes from the target's plate-solved standard stack taken with the
same camera and telescope (see `target_center_hint`). The pointing error of a
spectral stack is the distance between its header position (the mount's
report) and the solved centre. Each target gives one error.

- The search radius is the largest error times a margin.
- The warning distance is the typical error plus a robust few standard
  deviations of the errors (at least one pixel), so the usual pointing errors
  pass and a far larger offset is reported.
- The fraction is the search radius in pixels, over the shorter side of the
  frame, times a margin for re-centering and drift.
"""

import logging
import os
import statistics
from collections.abc import Sequence
from typing import Any

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib._internal.drivers.camera_profile_store import camera_identity
from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.astrometry.processing.simbad_matching import (
    CENTRE_STAR_SEARCH_RADIUS_FRACTION,
    HINT_MATCH_WARNING_ARCSEC,
    POINTING_ERROR_SEARCH_RADIUS_ARCSEC,
)
from astrometricslib.pipelines.shared.target_center_hint import (
    resolve_solved_stack_center_hint,
    resolve_solved_stack_wcs,
)
from astrometricslib.utilities.robust_statistics import robust_sigma

logger = logging.getLogger(__name__)

ARCSEC_PER_DEGREE = 3600.0

# How many times the largest pointing error seen the search radius is. The
# evidence (AST-09) has a largest error of 138 arcsec and a mislabel 145
# arcsec away; the 300 arcsec in use is about twice the first, which leaves
# room for a night with worse pointing than any seen.
POINTING_SEARCH_MARGIN = TunedParameter(
    name="astrometry.pointing_search_margin",
    default=2.0,
    unit="× largest pointing error",
    description="How many times the largest pointing error seen the search radius is set to.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-09",
)

# How many times the pointing error, in pixels, the centre search covers. The
# target can also sit off the frame's centre because the frame was re-centered
# or drifted: on 2026-10-04 the Sirius zero order sat 151 pixels from the
# centre against the 157 pixels that the mount's error alone allows, so the
# search needs about twice the pointing error to cover both.
CENTRE_SEARCH_RECENTERING_MARGIN = TunedParameter(
    name="astrometry.centre_search_recentering_margin",
    default=2.0,
    unit="× search radius in pixels",
    description="How many times the pointing search radius, in pixels, the centre-star search covers.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-09",
)

# How many robust standard deviations above the typical pointing error the
# hint match warning sits. Five is the repository's usual cut for an outlier
# against the spread of its own data. On the four stacks of AST-09 (errors of
# 22, 24, 24 and 138 arcsec) it gives about 30 arcsec.
HINT_WARNING_SIGMAS = TunedParameter(
    name="astrometry.hint_warning_sigmas",
    default=5.0,
    unit="× robust standard deviation",
    description="How many robust standard deviations above the typical pointing error the warning sits.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-D14",
)


def pointing_error_arcsec(
    mount_ra_deg: float, mount_dec_deg: float, solved_ra_deg: float, solved_dec_deg: float
) -> float:
    """Measure how far the mount's reported position is from the solved centre.

    Parameters
    ----------
    mount_ra_deg, mount_dec_deg : `float`
        The position in the spectral stack's header, in decimal degrees.
    solved_ra_deg, solved_dec_deg : `float`
        The centre of the plate-solved stack, in decimal degrees.

    Returns
    -------
    error_arcsec : `float`
        The angle between the two positions, in arcseconds.
    """
    mount = SkyCoord(mount_ra_deg * u.deg, mount_dec_deg * u.deg)
    solved = SkyCoord(solved_ra_deg * u.deg, solved_dec_deg * u.deg)
    return float(mount.separation(solved).arcsec)


def pointing_limits(
    errors_arcsec: Sequence[float],
    pixel_scales_arcsec: Sequence[float],
    shorter_sides_px: Sequence[float],
    search_margin: float,
    recentering_margin: float,
    warning_sigmas: float,
) -> dict[str, float]:
    """Work out the three limits from the pointing errors seen.

    Parameters
    ----------
    errors_arcsec : `Sequence` [`float`]
        The pointing error of each spectral stack.
    pixel_scales_arcsec : `Sequence` [`float`]
        The pixel scale of each stack, in arcseconds per pixel.
    shorter_sides_px : `Sequence` [`float`]
        The shorter side of each stack, in pixels.
    search_margin : `float`
        Times the largest error, for the search radius.
    recentering_margin : `float`
        Times the search radius in pixels, for the centre search.
    warning_sigmas : `float`
        Robust standard deviations above the median error, for the warning.

    Returns
    -------
    values : `dict` [`str`, `float`]
        The search radius, the centre search fraction and the warning
        distance, by parameter name. The warning's spread is at least one
        pixel, so identical errors do not give a warning at the error itself.
    """
    radius_arcsec = search_margin * max(errors_arcsec)
    pixel_scale = statistics.median(pixel_scales_arcsec)
    fraction = recentering_margin * (radius_arcsec / pixel_scale) / statistics.median(shorter_sides_px)
    spread_arcsec = max(robust_sigma(errors_arcsec), pixel_scale)
    warning_arcsec = statistics.median(errors_arcsec) + warning_sigmas * spread_arcsec
    return {
        POINTING_ERROR_SEARCH_RADIUS_ARCSEC.name: radius_arcsec,
        CENTRE_STAR_SEARCH_RADIUS_FRACTION.name: fraction,
        HINT_MATCH_WARNING_ARCSEC.name: warning_arcsec,
    }


def _mount_position(header: Any) -> tuple[float, float] | None:
    """Read the mount's reported position from a stack header.

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        Right ascension and declination in decimal degrees, or `None` when the
        header has no usable ``RA`` and ``DEC``.
    """
    try:
        return float(header["RA"]), float(header["DEC"])
    except KeyError, TypeError, ValueError:
        return None


def tune_pointing_limits(inputs: TuningInputs) -> TuningResult:
    """Derive the pointing search limits from the library's spectral stacks.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The search radius, the centre search fraction and the warning
        distance, resting on the number of targets measured.

    Raises
    ------
    TuningSkippedError
        If too few spectral stacks of the setup's camera have a solved
        standard stack and a mount position to compare.
    """
    astrometrics = inputs.service("astrometrics")
    identity = camera_identity(inputs.setup.camera_name, inputs.configuration)
    errors: list[float] = []
    scales: list[float] = []
    sides: list[float] = []
    for target in astrometrics.targets.list():
        path = target.spectral_stacking.stacked_image
        if not path or not os.path.exists(path):
            continue
        try:
            header = read_header(path)
        except OSError:
            continue
        camera = header.get("INSTRUME")
        mount_position = _mount_position(header)
        if (
            not camera
            or mount_position is None
            or camera_identity(str(camera), inputs.configuration) != identity
        ):
            continue
        solved_ra, solved_dec = resolve_solved_stack_center_hint(target, path)
        solved_wcs = resolve_solved_stack_wcs(target, path)
        if solved_ra is None or solved_dec is None or solved_wcs is None:
            continue
        errors.append(pointing_error_arcsec(*mount_position, solved_ra, solved_dec))
        scales.append(float(proj_plane_pixel_scales(solved_wcs).mean()) * ARCSEC_PER_DEGREE)
        sides.append(float(min(header.get("NAXIS1", 0), header.get("NAXIS2", 0))))
    needed = max(parameter.minimum_sample or 1 for parameter in TUNER_PARAMETERS)
    if len(errors) < needed or min(sides, default=0.0) <= 0:
        raise TuningSkippedError(
            f"{len(errors)} spectral stacks of {inputs.setup.camera_name} have a mount position and a "
            f"plate-solved standard stack to compare; at least {needed} are needed"
        )
    values = pointing_limits(
        errors,
        scales,
        sides,
        resolve(POINTING_SEARCH_MARGIN).value,
        resolve(CENTRE_SEARCH_RECENTERING_MARGIN).value,
        resolve(HINT_WARNING_SIGMAS).value,
    )
    return TuningResult(
        values=values,
        sample=len(errors),
        source=(
            f"Pointing error (mount position against the solved standard stack's centre) of {len(errors)} "
            f"spectral stacks of {inputs.setup.camera_name}; largest {max(errors):.0f} arcsec"
        ),
    )


TUNER_PARAMETERS = (
    POINTING_ERROR_SEARCH_RADIUS_ARCSEC,
    CENTRE_STAR_SEARCH_RADIUS_FRACTION,
    HINT_MATCH_WARNING_ARCSEC,
)

POINTING_TUNER = Tuner(
    name="astrometry.pointing_error",
    parameters=TUNER_PARAMETERS,
    method=(
        "Measures, for each spectral stack of the setup's camera, the distance between its header "
        "position (the mount's report) and the centre of the target's plate-solved standard stack; the "
        "search radius is a margin above the largest error, the warning a robust fence above the typical "
        "error, and the centre fraction the radius in pixels with a margin for re-centering."
    ),
    tune=tune_pointing_limits,
)
