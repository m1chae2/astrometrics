"""Tune how far the standard star may sit from the frame center.

A standard-star script takes the detected source nearest the frame center as
the standard star, and refuses when that source is farther than a limit (see
`standard_star_selection`). The limit must be wide enough for the standard
star's real pointing error and narrow enough to keep out the nearest wrong
source.

This tuner measures both sides in the library. For every target whose own
star has a stored spectrum, the star's zero-order position in the spectral
stack gives the real offset from the frame center. The other stars with
stored spectra in the same stack give the distance of the nearest wrong
source. The limit lies halfway between the high end of the real offsets and
the low end of the nearest wrong distances. When the two overlap, no limit
separates them and the tuner stays silent.
"""

import logging
import re
from typing import Any

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.standard_star_selection import (
    MAXIMUM_STAR_OFFSET_FROM_CENTER_PX,
)
from astrometricslib.pipelines.spectroscopy.tuning_data import quantile_of, spectral_target_stars

logger = logging.getLogger(__name__)

# The share of real standard stars the limit must accept, and (as the same
# share from the other end) the share of nearest wrong sources it must
# refuse. Nineteen in twenty keeps one unusual pointing error or one close
# neighbor from deciding the limit.
_STANDARD_STAR_COVERAGE = TunedParameter(
    name="spectroscopy.standard_star_tuning.coverage",
    default=0.95,
    unit="fraction",
    description="The share of real standard stars accepted and of nearest wrong sources refused.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="SPE-D52",
)


def offset_from_center(position_px: tuple[float, float], image_shape: tuple[int, int]) -> float:
    """Measure how far a position is from the frame center.

    Parameters
    ----------
    position_px : `tuple` [`float`, `float`]
        The ``(x, y)`` pixel position.
    image_shape : `tuple` [`int`, `int`]
        The image's ``(height, width)`` in pixels.

    Returns
    -------
    offset_px : `float`
        The distance from the center, in pixels.
    """
    height, width = image_shape
    return float(np.hypot(position_px[0] - width / 2, position_px[1] - height / 2))


def separating_limit(
    real_offsets: list[float], nearest_wrong_distances: list[float], coverage: float
) -> float | None:
    """Place the limit between real offsets and the nearest wrong sources.

    Parameters
    ----------
    real_offsets : `list` [`float`]
        The standard star's offset from the center in each target, in pixels.
    nearest_wrong_distances : `list` [`float`]
        The distance of the nearest other source in each target, in pixels.
    coverage : `float`
        The share of real offsets to accept; the same share of wrong
        distances is refused.

    Returns
    -------
    limit_px : `float` or `None`
        The midpoint between the coverage quantile of the real offsets and
        the opposite quantile of the wrong distances, or `None` when the
        real offsets reach as far as the wrong distances.
    """
    high_real = quantile_of(real_offsets, coverage)
    low_wrong = quantile_of(nearest_wrong_distances, 1.0 - coverage)
    if high_real >= low_wrong:
        return None
    return 0.5 * (high_real + low_wrong)


def _normalized(text: str) -> str:
    """Reduce an id to lowercase letters and digits.

    Returns
    -------
    text : `str`
        The id without case, spaces or punctuation.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def tune_standard_star_offset(inputs: TuningInputs) -> TuningResult:
    """Tune the standard-star offset limit from stored zero-order positions.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and the ``"astrometrics"`` service.

    Returns
    -------
    result : `TuningResult`
        The limit, from the targets whose own star has a stored spectrum.

    Raises
    ------
    TuningSkippedError
        If no target has both its own star and another source with a
        stored zero-order position, or if the real offsets and the wrong
        distances overlap.
    """
    astrometrics: Any = inputs.service("astrometrics")
    real_offsets: list[float] = []
    nearest_wrong: list[float] = []
    for entry in spectral_target_stars(astrometrics, inputs.setup.camera_name, inputs.configuration):
        own_key = _normalized(entry.target_id)
        own_offset = None
        wrong_distances = []
        for star in entry.stars:
            position = star.spectroscopy.star_position_px if star.has_spectra else None
            if not position or len(position) < 2:
                continue
            offset = offset_from_center((float(position[0]), float(position[1])), entry.image_shape)
            if _normalized(star.id) == own_key or _normalized(star.name or "") == own_key:
                own_offset = offset
            else:
                wrong_distances.append(offset)
        if own_offset is not None and wrong_distances:
            real_offsets.append(own_offset)
            nearest_wrong.append(min(wrong_distances))
    if not real_offsets:
        raise TuningSkippedError(
            "no target has its own star and another source with stored zero-order positions"
        )
    limit = separating_limit(real_offsets, nearest_wrong, resolve(_STANDARD_STAR_COVERAGE).value)
    if limit is None:
        raise TuningSkippedError(
            "the standard stars' offsets reach as far as the nearest wrong sources, "
            "so no limit separates them"
        )
    return TuningResult(
        values={MAXIMUM_STAR_OFFSET_FROM_CENTER_PX.name: round(limit, 1)},
        sample=len(real_offsets),
        source=(
            f"Midway between the real offsets of {len(real_offsets)} standard stars from the frame center "
            "and the distances of the nearest other source in the same stacks"
        ),
    )


STANDARD_STAR_OFFSET_TUNER = Tuner(
    name="spectroscopy.standard_star_offset",
    parameters=(MAXIMUM_STAR_OFFSET_FROM_CENTER_PX,),
    method=(
        "Takes the stored zero-order offset from the frame center of each target's own star and the "
        "distance of the nearest other source in the same stack. The limit is halfway between the high "
        "end of the first and the low end of the second."
    ),
    tune=tune_standard_star_offset,
)
