"""Purpose: Answer where things are in the sky, for ``wayfinder.planning.sky``.

Description: The owner functions behind the sky object of planning:

* `get_visibility`: where objects are at one moment or over a span, with the
  night's twilight and Moon for a span (`visibility_report` does the sums).
* `get_tonight_advice`: the targets ranked by what the next hour of each would
  add (`tonight_ranking_tasks`).
* `get_constellation_lines`: the bundled constellation stick figures.
* `get_panels`: the panel centers of a mosaic grid around an object
  (`mosaic_tasks`).

Nothing here is stored, and nothing talks to hardware.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import ValidationError

from astrometricslib import ObservatoryConfiguration, StellarObject, Target
from astrometricslib.arguments import TimeInput, check_include, to_epoch_seconds
from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.models.planning.mosaic import MosaicPanel
from wayfindinglib.models.planning.sky_catalogs import ConstellationLineSegment
from wayfindinglib.models.planning.tonight_ranking import TonightRanking
from wayfindinglib.models.planning.visibility import HorizonZone, VisibilityReport
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = ["get_constellation_lines", "get_panels", "get_tonight_advice", "get_visibility"]


def _check_objects(objects: Sequence[Target | StellarObject] | None) -> None:
    """Refuse anything but library targets and stars as sky objects.

    Parameters
    ----------
    objects : `Sequence` or `None`
        The objects asked about.

    Raises
    ------
    InvalidArgumentError
        If ``objects`` is text, or an item is not a `Target` or
        `StellarObject`.
    """
    if objects is None:
        return
    if isinstance(objects, str) or not isinstance(objects, Sequence):
        raise InvalidArgumentError("objects must be a list of Target or StellarObject objects.")
    wrong = [repr(item) for item in objects if not isinstance(item, Target | StellarObject)]
    if wrong:
        raise InvalidArgumentError(
            "Each object must be a Target or a StellarObject; turn a name into one with "
            "astrometrics.targets.resolve(name).",
            details={"objects": wrong[:5]},
        )


def get_visibility(
    context: PlanningContext,
    objects: Sequence[Target | StellarObject] | None = None,
    time: TimeInput | None = None,
    until: TimeInput | None = None,
    step_min: float | None = None,
    include: Sequence[str] = (),
    minimum_altitude_deg: float = 0.0,
    horizon_zones: Sequence[HorizonZone] = (),
    clear_only: bool = False,
) -> VisibilityReport:
    """Say where objects are in the sky, at one moment or over a span.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    objects : `Sequence` [`Target` or `StellarObject`], optional
        The objects. Every library target, highest first, when omitted.
    time : `TimeInput`, optional
        The moment, or the start of the span. Now when omitted.
    until : `TimeInput`, optional
        The end of the span. Omit it for one moment.
    step_min : `float`, optional
        Minutes between rows of a span, at least 1. Defaults to 30.
    include : `Sequence` [`str`], optional
        ``"meridian"`` and, for a span, ``"samples"``.
    minimum_altitude_deg : `float`, optional
        The lowest altitude that counts as clear.
    horizon_zones : `Sequence` [`HorizonZone`], optional
        Blocked parts of the sky.
    clear_only : `bool`, optional
        Keep only the objects clear of the horizon limit at ``time``.

    Returns
    -------
    report : `VisibilityReport`
        The site, the horizon limit, one entry per object, and for a span
        the night itself. Times are UTC.

    Raises
    ------
    InvalidArgumentError
        If an object is not a target or star, a time cannot be read, a
        span-only argument comes without ``until``, a zone is not valid, or
        the span is too long or too crowded.
    """
    from wayfindinglib.tasks.planning_tasks import visibility_report

    _check_objects(objects)
    sections = check_include(include, visibility_report.INCLUDE_SECTIONS)
    try:
        zones = [HorizonZone.model_validate(zone) for zone in horizon_zones]
    except ValidationError as error:
        raise InvalidArgumentError(
            "Each horizon zone needs azimuth_start_deg, azimuth_end_deg and min_clear_altitude_deg.",
            details={"error": str(error)},
        ) from error
    start = visibility_report.to_astropy_time(time, "time")
    end = visibility_report.to_astropy_time(until, "until") if until is not None else None
    resolved = visibility_report.resolve_sky_objects(
        context.sky_engine, None if objects is None else list(objects)
    )
    report = visibility_report.build_visibility_report(
        context.sky_engine, resolved, start, end, step_min, sections, minimum_altitude_deg, zones, 0.0
    )
    if clear_only:
        report.objects = [entry for entry in report.objects if entry.clear]
    if objects is None:
        report.objects.sort(key=lambda entry: entry.altitude_deg, reverse=True)
    return report


def get_tonight_advice(context: PlanningContext, time: TimeInput | None = None) -> TonightRanking:
    """Rank the targets by what the next hour of each would add.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    time : `TimeInput`, optional
        The time to rank for. Now when omitted.

    Returns
    -------
    ranking : `TonightRanking`
        The ranked candidate targets.
    """
    from wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks import rank_tonight

    moment = (
        datetime.now(UTC) if time is None else datetime.fromtimestamp(to_epoch_seconds(time, "time"), UTC)
    )
    return rank_tonight(
        moment, astrometrics=context.astrometrics, butler=context.butler, config=context.config
    )


def get_constellation_lines(context: PlanningContext) -> list[ConstellationLineSegment]:
    """Return the constellation stick-figure line segments.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.

    Returns
    -------
    lines : `list` [`ConstellationLineSegment`]
        Every straight piece of every constellation's stick figure (about
        750 from the bundled file).
    """
    return [
        ConstellationLineSegment.model_validate(row) for row in context.sky_engine.get_constellation_lines()
    ]


def get_panels(
    context: PlanningContext,
    center: Target | StellarObject,
    rows: int = 1,
    cols: int = 1,
    overlap_pct: float = 0.0,
    rotation_deg: float = 0.0,
    configuration: ObservatoryConfiguration | None = None,
) -> list[MosaicPanel]:
    """Work out the center of each panel of a mosaic grid.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    center : `Target` or `StellarObject`
        The object at the grid center.
    rows, cols : `int`, optional
        The grid size.
    overlap_pct : `float`, optional
        How much neighboring panels overlap, from 0 to under 100.
    rotation_deg : `float`, optional
        How far the camera is turned, in degrees east of north.
    configuration : `ObservatoryConfiguration`, optional
        The equipment whose field of view sets the spacing. The configured
        sensor and focal length when omitted.

    Returns
    -------
    panels : `list` [`MosaicPanel`]
        One panel per grid cell, row by row.

    Raises
    ------
    InvalidArgumentError
        If the center has no position, or the grid, overlap or rotation is
        out of range.
    """
    from wayfindinglib.tasks.planning_tasks.mosaic_tasks import calculate_panels
    from wayfindinglib.tasks.planning_tasks.visibility_report import _object_coordinates

    _check_objects([center])
    coordinates = _object_coordinates(center)
    if coordinates is None:
        raise InvalidArgumentError(f"{center.id} has no sky position.", details={"center": center.id})
    ra_deg, dec_deg = coordinates
    return calculate_panels(
        context.config, ra_deg, dec_deg, rows, cols, overlap_pct, configuration, rotation_deg
    )
