"""Tuners for the star-elongation limits of the frame quarantine.

The quarantine moves a frame aside as trailed when its star elongation (the
long width of its stars divided by the short width) is above a limit (see
`frame_quarantine.judge_batch`). Three tuned values set that limit: a fixed
elongation (`TRAILED_ELONGATION_LIMIT`), and how far above the batch's median
a frame must be (`ELONGATION_RISE_ABOVE_MEDIAN` for a monochrome camera,
`COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN` for a colour camera, whose small,
coarsely sampled stars measure less steadily).

They are derived from the stored `frame.star_elongation` row of every frame
the library has measured with the setup's camera (register entries STK-01 and
STK-02). A trailed frame is a rare outlier among round frames, so the limits
are placed a fixed number of robust standard deviations above the typical
elongation. The spread is the median absolute deviation (see
`tuning_support.robust_sigma`), which a few trailed frames cannot inflate.
The spread is taken within each session, because the typical elongation
differs from session to session (focus, seeing) and that difference is not
trailing.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from astrometricslib._internal.drivers.tuning_support import camera_is_colour, camera_rows
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.metrics import FRAME_STAR_ELONGATION
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN,
    ELONGATION_RISE_ABOVE_MEDIAN,
    MINIMUM_FRAMES_TO_JUDGE,
    TRAILED_ELONGATION_LIMIT,
)
from astrometricslib.utilities.robust_statistics import robust_sigma

# How many robust standard deviations above the typical elongation a frame must
# be to count as trailed. Five is the repository's usual cut for an outlier
# against the spread of its own run (see `stacking.step_jump_sigmas`): with a
# known spread, a good frame passes it with probability about 3e-7, and the
# spread is estimated from the same frames, so a few false alarms remain
# possible. The trailed frames of the evidence (STK-01) sit beyond it, except
# a few borderline ones (NGC 7331 L_120, 1.306 against a cut of 1.32).
ELONGATION_OUTLIER_SIGMAS = TunedParameter(
    name="stacking.elongation_outlier_sigmas",
    default=5.0,
    unit="× robust standard deviation",
    description="How many robust standard deviations above the typical elongation a frame counts as trailed.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-01",
)


# How many robust standard deviations above its own session's median a frame of
# a monochrome camera must be to count as trailed. It is smaller than the
# outlier cut above because the contact sheets of 2026-11-20 (STK-01) show
# trailing well inside five spreads in sessions whose median is already above
# 1.2: M 27 of 2026-09-24 frames 057 and 059 (+0.14 and +0.13 above the
# median of 1.28) and NGC 2903 frame 015 (+0.12, doubled stars) are trailed,
# while frames up to +0.11 (NGC 2403 022, 028) look like the rest of their
# session. 0.12 over the spread of 0.045 is 2.7 spreads. The fixed limit and
# the colour camera's rise keep the five-spread cut.
ELONGATION_RISE_SIGMAS = TunedParameter(
    name="stacking.elongation_rise_sigmas",
    default=2.7,
    unit="× robust standard deviation",
    description="Robust standard deviations above its session's median at which a mono frame is trailed.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-01",
)


def group_elongation_by_session(rows: Iterable[QualityMeasurement]) -> list[list[float]]:
    """Gather the stored elongation of frames into sessions.

    A session is the frames of one target taken on one observing night with
    one capture setting, which is what the quarantine judges as a batch.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Stored elongation rows. A row with no value is left out.

    Returns
    -------
    sessions : `list` [`list` [`float`]]
        The elongation values of each session that has at least
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


def rise_above_session_median(sessions: Sequence[Sequence[float]], sigmas: float) -> float:
    """Find how far above its own session's median a frame may sit.

    Parameters
    ----------
    sessions : `Sequence` [`Sequence` [`float`]]
        The elongation values of each session.
    sigmas : `float`
        How many robust standard deviations above the session median.

    Returns
    -------
    rise : `float`
        ``sigmas`` times the robust standard deviation of every frame's
        distance from its own session's median. Sessions that are mildly
        elongated throughout, or noisy to measure, give a wider rise.
    """
    offsets = [value - statistics.median(session) for session in sessions for value in session]
    return sigmas * robust_sigma(offsets)


def fixed_elongation_limit(sessions: Sequence[Sequence[float]], sigmas: float) -> float:
    """Find the elongation above which a frame is a library outlier.

    Parameters
    ----------
    sessions : `Sequence` [`Sequence` [`float`]]
        The elongation values of each session.
    sigmas : `float`
        How many robust standard deviations above the typical elongation.

    Returns
    -------
    limit : `float`
        The median of all the elongation values plus the rise
        (`rise_above_session_median`). The spread is the one inside sessions,
        because the pooled spread also holds the difference between sessions
        (focus, seeing), which is not trailing.
    """
    pooled = [value for session in sessions for value in session]
    return statistics.median(pooled) + rise_above_session_median(sessions, sigmas)


def _sessions_of_setup(inputs: TuningInputs) -> list[list[float]]:
    """Read the elongation sessions of the setup's camera.

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
        If the camera has no stored elongation rows in a session that large.
    """
    rows = camera_rows(
        inputs.configuration, inputs.setup.camera_name, FRAME_STAR_ELONGATION, level=SubjectLevel.FRAME
    )
    sessions = group_elongation_by_session(row for row, _ in rows)
    if not sessions:
        raise TuningSkippedError(
            f"no session of {resolve(MINIMUM_FRAMES_TO_JUDGE).value} or more frames of "
            f"{inputs.setup.camera_name} has a stored star elongation"
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
    """Derive the fixed elongation limit, and the rise for a monochrome camera.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The fixed limit always; the monochrome rise only when the setup's
        camera is monochrome (a colour camera uses its own rise).

    """
    astrometrics = inputs.service("astrometrics")
    sessions = _sessions_of_setup(inputs)
    sigmas = resolve(ELONGATION_OUTLIER_SIGMAS).value
    values: dict[str, float] = {TRAILED_ELONGATION_LIMIT.name: fixed_elongation_limit(sessions, sigmas)}
    if not _camera_is_colour(inputs, astrometrics):
        values[ELONGATION_RISE_ABOVE_MEDIAN.name] = rise_above_session_median(
            sessions, resolve(ELONGATION_RISE_SIGMAS).value
        )
    frame_count = sum(len(session) for session in sessions)
    return TuningResult(
        values=values,
        sample=len(sessions),
        source=(
            f"Robust {sigmas:g}-sigma limits on the stored star elongation of {frame_count} frames in "
            f"{len(sessions)} sessions of {inputs.setup.camera_name}"
        ),
    )


def tune_colour_camera_drop(inputs: TuningInputs) -> TuningResult:
    """Derive the elongation rise of a colour camera.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The colour camera's rise above the session median.

    Raises
    ------
    TuningSkippedError
        If the setup's camera is not a colour camera, or the library has too
        little stored elongation data for it.
    """
    astrometrics = inputs.service("astrometrics")
    if not _camera_is_colour(inputs, astrometrics):
        raise TuningSkippedError(f"{inputs.setup.camera_name} is not a colour camera")
    sessions = _sessions_of_setup(inputs)
    sigmas = resolve(ELONGATION_OUTLIER_SIGMAS).value
    frame_count = sum(len(session) for session in sessions)
    return TuningResult(
        values={COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN.name: rise_above_session_median(sessions, sigmas)},
        sample=len(sessions),
        source=(
            f"Robust {sigmas:g}-sigma rise above the session median of the stored star elongation of "
            f"{frame_count} frames in {len(sessions)} sessions of {inputs.setup.camera_name}"
        ),
    )


TRAILED_LIMITS_TUNER = Tuner(
    name="stacking.trailed_elongation_limits",
    parameters=(TRAILED_ELONGATION_LIMIT, ELONGATION_RISE_ABOVE_MEDIAN),
    method=(
        "Reads the stored star elongation of every frame of the setup's camera, groups the frames into "
        "sessions (one target, night and capture setting, at least the quarantine's minimum batch), and "
        "sets the rise above the session median a set number of robust standard deviations out (fewer for "
        "the rise than for the fixed limit). The fixed limit is the library median plus five spreads."
    ),
    tune=tune_trailed_limits,
)

COLOUR_DROP_TUNER = Tuner(
    name="stacking.colour_camera_elongation_rise",
    parameters=(COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN,),
    method=(
        "For a colour camera only: reads the stored star elongation of its frames and sets the rise above "
        "the session median a set number of robust standard deviations out."
    ),
    tune=tune_colour_camera_drop,
)
