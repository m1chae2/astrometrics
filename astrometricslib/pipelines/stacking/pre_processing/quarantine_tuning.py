"""Tuners for the roundness limits of the frame quarantine.

The quarantine moves a frame aside as trailed when its median star roundness
is below a limit (see `frame_quarantine.judge_batch`). Three tuned values set
that limit: a fixed roundness (`TRAILED_ROUNDNESS_LIMIT`), and how far below
the batch's median a frame must be (`ROUNDNESS_DROP_BELOW_MEDIAN` for a
monochrome camera, `COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN` for a colour
camera, whose small, coarsely sampled stars measure less steadily).

Both are derived from the stored `frame.quarantine_roundness` row of every
frame the library has measured with the setup's camera (register entries
STK-01 and STK-02). A trailed frame is a rare outlier among round frames, so
the limits are placed a fixed number of robust standard deviations below the
typical roundness. The spread is the median absolute deviation (see
`tuning_support.robust_sigma`), which a few trailed frames cannot inflate.
The frames that the quarantine moved keep their rows, so they are in the data.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from astrometricslib._internal.drivers.tuning_support import camera_is_colour, camera_rows, robust_sigma
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.metrics import FRAME_QUARANTINE_ROUNDNESS
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN,
    MINIMUM_FRAMES_TO_JUDGE,
    ROUNDNESS_DROP_BELOW_MEDIAN,
    TRAILED_ROUNDNESS_LIMIT,
)

# How many robust standard deviations below the typical roundness a frame must
# be to count as trailed. Five is the repository's usual cut for an outlier
# against the spread of its own run (see `stacking.step_jump_sigmas`): with a
# known spread, a good frame passes it with probability about 3e-7, and the
# spread is estimated from the same frames, so a few false alarms remain
# possible. The trailed frames of the evidence (STK-01) sit far beyond it.
ROUNDNESS_OUTLIER_SIGMAS = TunedParameter(
    name="stacking.roundness_outlier_sigmas",
    default=5.0,
    unit="× robust standard deviation",
    description="How many robust standard deviations below the typical roundness a frame counts as trailed.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-01",
)


def group_roundness_by_session(rows: Iterable[QualityMeasurement]) -> list[list[float]]:
    """Gather the stored roundness of frames into sessions.

    A session is the frames of one target taken on one observing night with
    one capture setting, which is what the quarantine judges as a batch.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Stored roundness rows. A row with no value is left out.

    Returns
    -------
    sessions : `list` [`list` [`float`]]
        The roundness values of each session that has at least
        `MINIMUM_FRAMES_TO_JUDGE` frames. Smaller sessions are too small for
        the quarantine to judge, so they say nothing about its limits.
    """
    minimum_frames = resolve(MINIMUM_FRAMES_TO_JUDGE).value
    sessions: dict[tuple[str | None, str | None, int | None], list[float]] = defaultdict(list)
    for row in rows:
        if row.value is None:
            continue
        key = (row.subject.target_id, row.subject.observing_night_id, row.subject.capture_setting_id)
        sessions[key].append(row.value)
    return [values for values in sessions.values() if len(values) >= minimum_frames]


def fixed_roundness_limit(sessions: Sequence[Sequence[float]], sigmas: float) -> float:
    """Find the roundness below which a frame is a library outlier.

    Parameters
    ----------
    sessions : `Sequence` [`Sequence` [`float`]]
        The roundness values of each session.
    sigmas : `float`
        How many robust standard deviations below the typical roundness.

    Returns
    -------
    limit : `float`
        The median of all the roundness values minus ``sigmas`` times their
        robust standard deviation.
    """
    pooled = [value for session in sessions for value in session]
    return statistics.median(pooled) - sigmas * robust_sigma(pooled)


def drop_below_session_median(sessions: Sequence[Sequence[float]], sigmas: float) -> float:
    """Find how far below its own session's median a frame may sit.

    Parameters
    ----------
    sessions : `Sequence` [`Sequence` [`float`]]
        The roundness values of each session.
    sigmas : `float`
        How many robust standard deviations below the session median.

    Returns
    -------
    drop : `float`
        ``sigmas`` times the robust standard deviation of every frame's
        distance below its own session's median. Sessions that are mildly
        elongated throughout, or noisy to measure, give a wider drop.
    """
    deficits = [statistics.median(session) - value for session in sessions for value in session]
    return sigmas * robust_sigma(deficits)


def _sessions_of_setup(inputs: TuningInputs) -> list[list[float]]:
    """Read the roundness sessions of the setup's camera.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    sessions : `list` [`list` [`float`]]
        The sessions of at least `MINIMUM_FRAMES_TO_JUDGE` frames.

    Raises
    ------
    TuningSkippedError
        If the camera has no stored roundness rows in a session that large.
    """
    rows = camera_rows(
        inputs.configuration, inputs.setup.camera_name, FRAME_QUARANTINE_ROUNDNESS, level=SubjectLevel.FRAME
    )
    sessions = group_roundness_by_session(row for row, _ in rows)
    if not sessions:
        raise TuningSkippedError(
            f"no session of {resolve(MINIMUM_FRAMES_TO_JUDGE).value} or more frames of "
            f"{inputs.setup.camera_name} has a stored roundness"
        )
    return sessions


def _camera_is_colour(inputs: TuningInputs, astrometrics: Any) -> bool:
    """Tell whether the setup's camera is a colour camera.

    Returns
    -------
    is_colour : `bool`
        The answer.

    Raises
    ------
    TuningSkippedError
        If no frame of the camera has a readable header.
    """
    is_colour = camera_is_colour(astrometrics, inputs.setup.camera_name, inputs.configuration)
    if is_colour is None:
        raise TuningSkippedError(f"no frame of {inputs.setup.camera_name} has a readable header")
    return is_colour


def tune_trailed_limits(inputs: TuningInputs) -> TuningResult:
    """Derive the fixed roundness limit, and the drop for a monochrome camera.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The fixed limit always; the monochrome drop only when the setup's
        camera is monochrome (a colour camera uses its own drop).

    """
    astrometrics = inputs.service("astrometrics")
    sessions = _sessions_of_setup(inputs)
    sigmas = resolve(ROUNDNESS_OUTLIER_SIGMAS).value
    values: dict[str, float] = {TRAILED_ROUNDNESS_LIMIT.name: fixed_roundness_limit(sessions, sigmas)}
    if not _camera_is_colour(inputs, astrometrics):
        values[ROUNDNESS_DROP_BELOW_MEDIAN.name] = drop_below_session_median(sessions, sigmas)
    frame_count = sum(len(session) for session in sessions)
    return TuningResult(
        values=values,
        sample=len(sessions),
        source=(
            f"Robust {sigmas:g}-sigma limits on the stored roundness of {frame_count} frames in "
            f"{len(sessions)} sessions of {inputs.setup.camera_name}"
        ),
    )


def tune_colour_camera_drop(inputs: TuningInputs) -> TuningResult:
    """Derive the roundness drop of a colour camera.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The colour camera's drop below the session median.

    Raises
    ------
    TuningSkippedError
        If the setup's camera is not a colour camera, or the library has too
        little stored roundness data for it.
    """
    astrometrics = inputs.service("astrometrics")
    if not _camera_is_colour(inputs, astrometrics):
        raise TuningSkippedError(f"{inputs.setup.camera_name} is not a colour camera")
    sessions = _sessions_of_setup(inputs)
    sigmas = resolve(ROUNDNESS_OUTLIER_SIGMAS).value
    frame_count = sum(len(session) for session in sessions)
    return TuningResult(
        values={COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN.name: drop_below_session_median(sessions, sigmas)},
        sample=len(sessions),
        source=(
            f"Robust {sigmas:g}-sigma drop below the session median of the stored roundness of "
            f"{frame_count} frames in {len(sessions)} sessions of {inputs.setup.camera_name}"
        ),
    )


TRAILED_LIMITS_TUNER = Tuner(
    name="stacking.trailed_roundness_limits",
    parameters=(TRAILED_ROUNDNESS_LIMIT, ROUNDNESS_DROP_BELOW_MEDIAN),
    method=(
        "Reads the stored roundness of every frame of the setup's camera, groups the frames into sessions "
        "(one target, night and capture setting, at least the quarantine's minimum batch), and sets the "
        "fixed limit and the drop below the session median a set number of robust standard deviations out."
    ),
    tune=tune_trailed_limits,
)

COLOUR_DROP_TUNER = Tuner(
    name="stacking.colour_camera_roundness_drop",
    parameters=(COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN,),
    method=(
        "For a colour camera only: reads the stored roundness of its frames and sets the drop below the "
        "session median a set number of robust standard deviations out."
    ),
    tune=tune_colour_camera_drop,
)
