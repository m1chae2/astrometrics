"""Tuner for the dark current allowance of the dark level check.

A dark frame should sit at the bias level of its setting, plus the dark
current it collected. The dark level check (`calibration_assessment.py`) adds
an allowance of `DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND` times the exposure to
the level difference it accepts (register entry STK-54).

The register entry measures the dark current as the slope of a dark frame's
mean level against its exposure, at one temperature and setting. This tuner
repeats that on the stored rows of the setup's camera:

1. Take each dark frame's stored clipped mean level, zero-pixel share and
   sensor temperature.
2. Group the frames by gain, offset, binning and temperature slot. Inside a
   group, take the median level of each exposure that has enough frames and
   no frames cut off at zero.
3. Fit a line of level against exposure with the median of the slopes between
   every pair of exposures (the Theil-Sen slope, which a step in the level at
   one exposure, such as a readout mode change, cannot bend). A group needs
   several different exposures.
4. The allowance is the median slope of the groups.
"""

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations

from astrometricslib._internal.drivers.calibration_library import temperature_slot_c
from astrometricslib._internal.drivers.tuning_support import camera_rows
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import CALIBRATION_SUBJECT_PREFIX
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_CLIPPED_MEAN_LEVEL,
    FRAME_SENSOR_TEMPERATURE,
    FRAME_ZERO_PIXEL_FRACTION,
)
from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
    DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND,
    THIN_SET_FRAME_COUNT,
    ZERO_PIXEL_FRACTION_LIMIT,
)

# A line fitted through fewer exposures says little: two points always fit,
# and three leave one point to check the line. Four different exposures leave
# a pair of checks and let the median slope ignore one exposure that is off.
MINIMUM_DARK_EXPOSURES = TunedParameter(
    name="stacking.dark_current_minimum_exposures",
    default=4,
    unit="exposures",
    description="Fewest different dark exposures at one setting for a dark current fit.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-54",
)

_DARK_PREFIX = f"{CALIBRATION_SUBJECT_PREFIX}/dark/"


def theil_sen_slope(exposures: Sequence[float], levels: Sequence[float]) -> float:
    """Fit a line's slope as the median pair slope.

    Parameters
    ----------
    exposures : `Sequence` [`float`]
        The x values (exposure, in seconds). At least two must differ.
    levels : `Sequence` [`float`]
        The y values (level, in ADU).

    Returns
    -------
    slope : `float`
        The median of ``(level_b - level_a) / (exposure_b - exposure_a)`` over
        the pairs whose exposures differ.
    """
    slopes = [
        (level_b - level_a) / (exposure_b - exposure_a)
        for (exposure_a, level_a), (exposure_b, level_b) in combinations(
            zip(exposures, levels, strict=True), 2
        )
        if exposure_b != exposure_a
    ]
    return statistics.median(slopes)


def dark_current_rate(
    levels_by_exposure: Mapping[float, Sequence[float]],
    zero_fractions_by_exposure: Mapping[float, Sequence[float]],
    minimum_frames: int,
    minimum_exposures: int,
    zero_limit: float,
) -> tuple[float, int] | None:
    """Fit the dark current of one setting and temperature.

    Parameters
    ----------
    levels_by_exposure : `Mapping` [`float`, `Sequence` [`float`]]
        The stored level of each dark frame, by exposure in seconds.
    zero_fractions_by_exposure : `Mapping` [`float`, `Sequence` [`float`]]
        The stored share of zero pixels of each dark frame, by exposure.
    minimum_frames : `int`
        Fewest frames an exposure needs.
    minimum_exposures : `int`
        Fewest different exposures the fit needs.
    zero_limit : `float`
        An exposure whose median zero share reaches this is cut off at zero
        and left out.

    Returns
    -------
    fit : `tuple` [`float`, `int`] or `None`
        The dark current in ADU per second and the number of exposures it
        rests on, or `None` when too few exposures are usable.
    """
    exposures: list[float] = []
    levels: list[float] = []
    for exposure, frame_levels in sorted(levels_by_exposure.items()):
        zero_fractions = zero_fractions_by_exposure.get(exposure, [])
        if len(frame_levels) < minimum_frames or not zero_fractions:
            continue
        if statistics.median(zero_fractions) >= zero_limit:
            continue
        exposures.append(exposure)
        levels.append(statistics.median(frame_levels))
    if len(exposures) < minimum_exposures:
        return None
    return theil_sen_slope(exposures, levels), len(exposures)


def tune_dark_current(inputs: TuningInputs) -> TuningResult:
    """Derive the dark current allowance from the stored dark frame levels.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The median dark current of the setting groups that could be fitted,
        resting on the number of exposure groups used.

    Raises
    ------
    TuningSkippedError
        If no setting group has enough exposures, or the dark current found
        is not above zero.
    """
    inputs.service("astrometrics")
    camera = inputs.setup.camera_name
    level_rows = camera_rows(
        inputs.configuration,
        camera,
        FRAME_CLIPPED_MEAN_LEVEL,
        level=SubjectLevel.FRAME,
        subject_id_prefix=_DARK_PREFIX,
    )
    zero_by_subject = {
        row.subject.subject_id: row.value
        for row, _ in camera_rows(
            inputs.configuration,
            camera,
            FRAME_ZERO_PIXEL_FRACTION,
            level=SubjectLevel.FRAME,
            subject_id_prefix=_DARK_PREFIX,
        )
    }
    temperature_by_subject = {
        row.subject.subject_id: row.value
        for row, _ in camera_rows(
            inputs.configuration,
            camera,
            FRAME_SENSOR_TEMPERATURE,
            level=SubjectLevel.FRAME,
            subject_id_prefix=_DARK_PREFIX,
        )
    }
    levels: dict[tuple, dict[float, list[float]]] = defaultdict(lambda: defaultdict(list))
    zeros: dict[tuple, dict[float, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row, setting in level_rows:
        subject_id = row.subject.subject_id
        slot = temperature_slot_c(temperature_by_subject.get(subject_id))
        if setting.exposure_seconds is None or slot is None or subject_id not in zero_by_subject:
            continue
        key = (setting.gain_setting, setting.offset, setting.binning, slot)
        levels[key][setting.exposure_seconds].append(row.value)
        zeros[key][setting.exposure_seconds].append(zero_by_subject[subject_id])
    minimum_frames = resolve(THIN_SET_FRAME_COUNT).value
    minimum_exposures = resolve(MINIMUM_DARK_EXPOSURES).value
    zero_limit = resolve(ZERO_PIXEL_FRACTION_LIMIT).value
    fits = [
        fit
        for key in levels
        if (fit := dark_current_rate(levels[key], zeros[key], minimum_frames, minimum_exposures, zero_limit))
        is not None
    ]
    if not fits:
        raise TuningSkippedError(
            f"no setting and temperature of {camera} has {minimum_exposures} dark exposures with "
            f"{minimum_frames} or more frames each that are not cut off at zero"
        )
    rate = statistics.median(rate for rate, _ in fits)
    if rate <= 0:
        raise TuningSkippedError(
            "the dark level does not rise with exposure, so no dark current is measurable"
        )
    exposure_count = sum(count for _, count in fits)
    return TuningResult(
        values={DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND.name: rate},
        sample=exposure_count,
        source=(
            f"Median Theil-Sen slope of dark level against exposure over {len(fits)} setting and "
            f"temperature groups ({exposure_count} exposure groups) of {camera}"
        ),
    )


DARK_CURRENT_TUNER = Tuner(
    name="stacking.dark_current_allowance",
    parameters=(DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND,),
    method=(
        "Reads the stored level, zero share and temperature of the camera's dark frames, groups them by "
        "gain, offset, binning and temperature slot, fits the level against exposure with the median pair "
        "slope, and takes the median slope of the groups."
    ),
    tune=tune_dark_current,
)
