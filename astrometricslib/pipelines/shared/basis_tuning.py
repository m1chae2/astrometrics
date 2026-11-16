"""Tuner for the typical star width of a setup (#175, Phase E3).

Before a run measures its own star width (for example inside the star-width
estimator, which cannot use the width it is about to measure), the values
that follow the star width use the setup's typical width,
`run_bases.TYPICAL_STAR_WIDTH_PX`. This tuner derives it from this
observatory's stored data: the median, over the setup's stored raw frames, of
each frame's Gaussian width of its brightest stars, which is the one
star-width scale (`run_bases.StarWidthEstimator`). The typical trail width of
a spectrum is tuned in `pipelines.spectroscopy.trail_width_tuning`.
"""

import statistics
from collections.abc import Iterable
from typing import Any

from astrometricslib._internal.drivers.tuning_support import camera_rows, finite_values
from astrometricslib.foundation.run_bases import TYPICAL_STAR_WIDTH_PX
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.metrics import FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX

# A stack's optic matches the setup's when its stored focal length is within
# this share of the optic's: the same 2% tolerance a frame's optic is chosen
# with (`frame_optics.FOCAL_LENGTH_TOLERANCE_FRACTION`).
OPTIC_FOCAL_LENGTH_TOLERANCE = TunedParameter(
    name="foundation.tuning_optic_focal_length_tolerance",
    default=0.02,
    unit="fraction of the focal length",
    description="How close a stored focal length must be to the setup optic's to count as that optic.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-17",
)


def widths_of_optic(
    rows: Iterable[tuple[Any, Any]], optic_name: str, focal_length_mm: float | None, tolerance: float
) -> list[float]:
    """Keep the newest width of each frame or stack taken through one optic.

    Parameters
    ----------
    rows : `Iterable` [`tuple`]
        ``(measurement, capture setting)`` pairs, oldest first.
    optic_name : `str`
        The setup's optic.
    focal_length_mm : `float` or `None`
        The optic's focal length, for settings that record no optic name.
    tolerance : `float`
        The share of the focal length within which a setting matches.

    Returns
    -------
    widths : `list` [`float`]
        One width per subject (its newest row), in pixels.
    """
    newest: dict[str, float] = {}
    for measurement, setting in rows:
        same_name = setting.telescope is not None and setting.telescope == optic_name
        same_focal = (
            focal_length_mm is not None
            and setting.focal_length_mm is not None
            and abs(setting.focal_length_mm - focal_length_mm) <= tolerance * focal_length_mm
        )
        if same_name or same_focal:
            newest[measurement.subject.subject_id] = float(measurement.value)
    return finite_values(newest.values())


def tune_typical_star_width(inputs: TuningInputs) -> TuningResult:
    """Derive the setup's typical star width from its stored raw frames.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and config.

    Returns
    -------
    result : `TuningResult`
        The median frame width, resting on the number of frames.

    Raises
    ------
    TuningSkippedError
        If the setup has no stored stack widths.
    """
    from astrometricslib.parameters import resolve_parameter

    optic = inputs.configuration.get_observatory_setups().optic_named(inputs.setup.optic_name)
    rows = camera_rows(inputs.configuration, inputs.setup.camera_name, FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX)
    widths = widths_of_optic(
        rows,
        inputs.setup.optic_name,
        optic.focal_length_mm if optic is not None else None,
        float(resolve_parameter(OPTIC_FOCAL_LENGTH_TOLERANCE).value),
    )
    if not widths:
        raise TuningSkippedError("no stored frame star widths for this camera and optic")
    return TuningResult(
        values={TYPICAL_STAR_WIDTH_PX.name: statistics.median(widths)},
        sample=len(widths),
        source=(
            f"Median Gaussian width of the brightest stars of {len(widths)} stored frames "
            "of this camera and optic"
        ),
    )


TYPICAL_STAR_WIDTH_TUNER = Tuner(
    name="foundation.typical_star_width",
    parameters=(TYPICAL_STAR_WIDTH_PX,),
    method="The median Gaussian width of the brightest stars over the setup's stored raw frames.",
    tune=tune_typical_star_width,
)


__all__ = [
    "TYPICAL_STAR_WIDTH_TUNER",
    "widths_of_optic",
]
