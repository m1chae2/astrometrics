"""Purpose: Review the star shapes of a target against the equipment's history.

Description: Reads the stored star-shape measurements of a target's frames
(and its stack, when the stack has them) and the frames the stacker set aside
as trailed, works out the equipment's own roundness limit for frames
(register entry WAY-42) and the library's stack baseline, and hands them to
`analytics.star_shape_review.judge_star_shape`. Nothing here writes.

The review is asked for on every visit to the Image Processing screen, so
its slow parts are kept in the context's `star_shape_cache` (a
`VersionedCache`) until the data behind them change:

* the target's stored star-shape numbers, until the target's own data
  change (`get_target_data_version`). When the only change is new quality
  rows (a new frame was measured), only those rows are read and merged in
  (`update_stored_shape_rows`); anything else reads them all again;
* the frame roundness limit, until the data it is worked out from change
  (`roundness_limit_data_version`): the session and guiding records, the
  equipment, site and configuration, and the measured frames of the nights
  it uses. A frame that is indexed but not yet measured does not rebuild it.
  Live guiding samples are counted only in coarse steps, as for a target
  report, so live guiding does not rebuild it on every call;
* the stack baseline, until any stack-level quality row changes
  (`get_stack_quality_data_version`).

The set-aside listing is not kept here; it is read once per review.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from astrometricslib import Target
from astrometricslib.arguments import FrameChoice, Page, observing_night_id
from astrometricslib.drivers import get_stack_quality_data_version, get_target_data_version
from astrometricslib.exceptions import FITS_READ_ERRORS, AstrometricsError, InvalidArgumentError
from wayfindinglib.analytics.capture_history import roundness_baseline_signature
from wayfindinglib.analytics.data_versions import report_context_version
from wayfindinglib.analytics.night_index import build_night_index
from wayfindinglib.analytics.star_shape_review import (
    ROUNDNESS_LIMIT_NAME,
    StoredShapeRows,
    judge_star_shape,
    read_stack_roundness_low_limit,
    read_star_shape_rows,
    read_stored_shape_rows,
    update_stored_shape_rows,
)
from wayfindinglib.models.session.star_shape_review import StarShapeReview
from wayfindinglib.tasks.control_tasks import night_analysis

if TYPE_CHECKING:
    from collections.abc import Hashable

    from wayfindinglib.api.control.context import ControlContext

logger = logging.getLogger(__name__)


def frame_paths_of(frames: FrameChoice | None) -> list[str] | None:
    """Turn a frame choice into the paths of its frames.

    Parameters
    ----------
    frames : `FrameChoice` or `None`
        A page or list of frame records, or a list of paths.

    Returns
    -------
    paths : `list` [`str`] or `None`
        The frames' paths, or `None` for no choice (every frame).

    Raises
    ------
    InvalidArgumentError
        If `frames` is a page cut short by its ``limit``.
    """
    if frames is None:
        return None
    if isinstance(frames, Page) and not frames.is_complete:
        raise InvalidArgumentError("frames is a page cut short by limit; ask for every matching frame.")
    return [str(getattr(frame, "path", frame)) for frame in frames]


def get_star_shape_review(
    context: ControlContext,
    target: Target,
    night: str | None = None,
    frames: FrameChoice | None = None,
) -> StarShapeReview:
    """Judge how round the stars of a target are, and what the stretch means.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the recorded sessions.
    target : `Target`
        The target.
    night : `str`, optional
        Judge only this observing night's frames. The roundness limit then
        uses only earlier nights, so a bad night cannot make itself normal.
    frames : `FrameChoice`, optional
        Judge only these frames.

    Returns
    -------
    review : `StarShapeReview`
        The verdicts, the set-aside frames, the typical elongation and the
        per-frame series.
    """
    frame_paths = frame_paths_of(frames)
    rows, stack, set_aside = read_star_shape_rows(
        context.astrometrics, target, night, frame_paths, stored=kept_shape_rows(context, target.id)
    )
    limit = kept_roundness_limit(context, night) if rows else None
    stack_limit = None
    if stack is not None and stack.elongation is not None:
        stack_limit = kept_stack_roundness_low_limit(context, stack.path)
    scope = "frame_list" if frame_paths is not None else ("night" if night is not None else None)
    return judge_star_shape(
        target.id,
        rows,
        stack,
        limit,
        scope,
        set_aside=set_aside,
        stack_roundness_low_limit=stack_limit,
    )


def kept_shape_rows(context: ControlContext, target_id: str) -> StoredShapeRows:
    """Give a target's stored star-shape numbers, read again only on change.

    When the target's data version changed only because quality rows were
    added (a new frame was measured, or an old frame measured again), the
    kept numbers are brought up to date by reading the added rows alone
    (`update_stored_shape_rows`). The answer is the same as a full read.
    Any other change, such as a deleted frame, reads every row again.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library, the configuration and the cache.
    target_id : `str`
        The target's id.

    Returns
    -------
    stored : `StoredShapeRows`
        The numbers. The same object is handed to every caller while the
        target's data (`get_target_data_version`) stay the same; it is not
        changed by anyone.
    """

    def read_version() -> Any:
        """Read the target's data version.

        Returns
        -------
        version : `Any`
            See `get_target_data_version`.
        """
        return get_target_data_version(context.config, target_id)

    def update(_kept_version: Any, kept: StoredShapeRows, version: Any) -> StoredShapeRows | None:
        """Read only the rows added since the kept numbers were read.

        Returns
        -------
        stored : `StoredShapeRows` or `None`
            The numbers for `version`, or `None` to read them all again.
        """
        return update_stored_shape_rows(context.astrometrics, target_id, kept, version)

    return context.star_shape_cache.get_or_build(
        ("star_shape_rows", target_id),
        read_version,
        lambda: read_stored_shape_rows(context.astrometrics, target_id, read_version=read_version),
        copy_result=False,
        update=update,
    )


def roundness_limit_data_version(context: ControlContext, night: str | None) -> Hashable | None:
    """Work out the version of the data the frame roundness limit uses.

    The limit (WAY-42) is the low limit of the nights' median roundness of
    this equipment's frames (`collect_capture_baseline_values`). It reads:

    * the light frames of the active telescope and camera, with their stored
      star width and elongation rows. Only the nights before `night` count,
      or every night when `night` is `None`. So with a night, a frame taken
      that night or later does not change the limit; without one, tonight's
      frames do, once tonight has enough measured frames;
    * the guide cadence, the median over every recorded night of this
      equipment's guiding, because only frames longer than a few guide
      cycles count. This reads the Ekos session records, the guiding runs
      and the guiding samples, including tonight's live ones;
    * the active equipment and site, and the configuration.

    Live samples can therefore move the limit, but only through tonight's
    share of the median cadence, and only when it moves the shortest
    counted exposure past a frame's exposure time. Rebuilding on every
    sample (every few seconds) would rebuild the whole envelope on every
    call during a night. So the records are watched as a target report
    watches them (`report_context_version`): every Ekos session record,
    guiding run, site record, guide log sync, equipment and configuration
    change counts exactly, and live samples count only in steps of
    ``REPORT_COARSE_STEP`` (WAY-D105, about 3.7 hours of guiding). That is
    the most a kept limit can lag behind the live samples; a guiding run
    being saved or a log sync ends the lag at once.

    The frames are watched exactly, but only those that can change a
    night's median roundness (`roundness_baseline_signature`): frames that
    are not spectral and have both a usable star width and a usable
    elongation, of nights with at least the minimum number of them, each
    with its night, exposure time and elongation. The frames come from the
    index of the nights (`build_night_index`, which keeps each target's
    frames until its document changes) and the kept measurements of every
    frame (`night_analysis.kept_frame_measurements`). So a frame that has
    just been indexed and not yet measured leaves the version as it is;
    its width and elongation rows, or a change of exposure time, spectral
    flag or night of a measured frame, change it. A frame that is removed
    or set aside leaves the target's frame list and changes it too.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration, the record store and the science library.
    night : `str` or `None`
        The night the limit is for, as in `kept_roundness_limit`.

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        Equal for two calls only when none of those inputs changed (apart
        from live samples within one coarse step). `None` when it cannot be
        worked out, so the limit is not kept.
    """
    records = report_context_version(context.config, context.butler)
    index = build_night_index(context, observing_night_id(time.time()) if night is None else night)
    if records is None or index is None:
        return None
    try:
        stored = night_analysis.kept_frame_measurements(context)
    except (AstrometricsError, sqlite3.Error, *FITS_READ_ERRORS) as error:
        logger.warning("Could not read the frames' stored measurements: %s", error)
        return None
    frames = index.frames if night is None else index.finished_frames
    return (records, night, roundness_baseline_signature(frames, stored.values, night))


def kept_roundness_limit(context: ControlContext, night: str | None) -> float | None:
    """Give the frame roundness limit, worked out again only on change.

    The limit (WAY-42) is one value of the performance envelope, which takes
    about a second to build. It is kept with `roundness_limit_data_version`,
    which follows only what the limit is worked out from, so live guiding
    samples and new frames of nights it does not use leave it as it is.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the active equipment, the recorded data and the cache.
    night : `str` or `None`
        Use only the nights before this one.

    Returns
    -------
    limit : `float` or `None`
        The limit, or `None` when no equipment is active or the history is
        too short.
    """

    def build() -> float | None:
        """Work out the envelope and read the limit from it.

        Returns
        -------
        limit : `float` or `None`
            The limit.
        """
        contexts, runs = night_analysis.recorded_sessions(context)
        envelope = night_analysis.performance_envelope(context, None, night, contexts, runs, {})
        return None if envelope is None else envelope.value(ROUNDNESS_LIMIT_NAME)

    return context.star_shape_cache.get_or_build(
        ("star_roundness_limit", night), lambda: roundness_limit_data_version(context, night), build
    )


def kept_stack_roundness_low_limit(context: ControlContext, stack_subject: str) -> float | None:
    """Give the stack baseline, worked out again only on change.

    The baseline reads the stored elongation of every library stack, so it
    is kept until any stack-level quality row changes
    (`get_stack_quality_data_version`). A new frame does not change it.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library, the configuration and the cache.
    stack_subject : `str`
        The stack being judged, which must not set its own limit.

    Returns
    -------
    limit : `float` or `None`
        See `read_stack_roundness_low_limit`.
    """
    return context.star_shape_cache.get_or_build(
        ("stack_roundness_low_limit", stack_subject),
        lambda: get_stack_quality_data_version(context.config),
        lambda: read_stack_roundness_low_limit(context.astrometrics, stack_subject),
    )
