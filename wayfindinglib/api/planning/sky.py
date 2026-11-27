"""Purpose: `PlanningSky`, where things are in the sky.

Reached as ``wayfinder.planning.sky``.

Description: `get_visibility` says where targets and stars are at one moment
or over a span, `get_tonight_advice` ranks the targets by what the next hour
of each would add, `get_constellation_lines` gives the constellation stick
figures and `get_panels` works out the panel centers of a mosaic grid.
Nothing is stored. Each method is a thin wrapper of one owner function in
`wayfindinglib.tasks.planning_tasks.sky_answers`.
"""

from astrometricslib import ObservatoryConfiguration, SkyPosition, StellarObject, Target
from astrometricslib.arguments import TimeInput
from wayfindinglib._internal.renames import MovedNames
from wayfindinglib.models.planning.mosaic import MosaicPanel
from wayfindinglib.models.planning.sky_catalogs import ConstellationLineSegment
from wayfindinglib.models.planning.tonight_ranking import TonightRanking
from wayfindinglib.models.planning.visibility import HorizonZone, VisibilityReport
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = ["PlanningSky"]


class PlanningSky(MovedNames):
    """Answers about the sky: visibility, tonight's ranking and mosaics.

    Parameters
    ----------
    context : `PlanningContext`
        The settings and storage the planning objects share.
    """

    def __init__(self, context: PlanningContext) -> None:
        self._context = context

    def get_visibility(
        self,
        objects: list[Target | StellarObject | SkyPosition] | None = None,
        *,
        time: TimeInput | None = None,
        until: TimeInput | None = None,
        step_min: float | None = None,
        include: list[str] | None = None,
        minimum_altitude_deg: float = 0.0,
        horizon_zones: list[HorizonZone] | None = None,
        clear_only: bool = False,
    ) -> VisibilityReport:
        """Say where objects are in the sky, at one moment or over a span.

        At one moment (no ``until``) each object gets its altitude, azimuth,
        whether it is above the horizon and clear of the horizon limit, and
        its rise, set and transit times. Over a span each object also gets
        its highest point, its meridian crossings and flip times, and when it
        is usable (clear while the Sun is below -18 degrees); the report also
        describes the night: twilight, the dark span and the Moon. A span
        covers at most 30 objects and 150 rows. Times are UTC. Nothing is
        stored.

        Owner:
            wayfindinglib.tasks.planning_tasks.sky_answers.get_visibility

        Parameters
        ----------
        objects : `list` [`Target`, `StellarObject` or `SkyPosition`], optional
            The objects. ``astrometrics.targets.resolve(name)`` turns a name
            into one; a `SkyPosition` stands for any point. Every library
            target, highest first, when omitted.
        time : `str`, `datetime` or `Time`, optional
            The moment, or the start of the span. Now when omitted. A time
            without a zone is UTC.
        until : `str`, `datetime` or `Time`, optional
            The end of the span. Omit it for one moment.
        step_min : `float`, optional
            Minutes between rows of a span, at least 1. Defaults to 30.
        include : `list` [`str`], optional
            ``"meridian"`` adds each object's hour angle and flip status.
            ``"samples"`` adds the row-by-row table of a span.
        minimum_altitude_deg : `float`, optional
            The lowest altitude that counts as clear. Defaults to 0.
        horizon_zones : `list` [`HorizonZone`], optional
            Blocked parts of the sky, such as trees.
        clear_only : `bool`, optional
            Keep only the objects clear of the horizon limit at ``time``.

        Returns
        -------
        report : `VisibilityReport`
            The site, the horizon limit, one entry per object, and for a span
            the night itself.

        Notes
        -----
        Raises `InvalidArgumentError` if an object is not a target, star or
        position, a
        time cannot be read, a span-only argument comes without ``until``, or
        the span is too long or too crowded.
        """
        from wayfindinglib.tasks.planning_tasks.sky_answers import get_visibility

        return get_visibility(
            self._context,
            objects,
            time,
            until,
            step_min,
            include or (),
            minimum_altitude_deg,
            horizon_zones or (),
            clear_only,
        )

    def get_tonight_advice(self, *, time: TimeInput | None = None) -> TonightRanking:
        """Rank the targets by what the next hour of each would add.

        The ranking is worked out on demand and never stored.

        Owner:
            wayfindinglib.tasks.planning_tasks.sky_answers.get_tonight_advice

        Parameters
        ----------
        time : `str`, `datetime` or `Time`, optional
            The time to rank for. Now when omitted.

        Returns
        -------
        ranking : `TonightRanking`
            The ranked candidate targets.
        """
        from wayfindinglib.tasks.planning_tasks.sky_answers import get_tonight_advice

        return get_tonight_advice(self._context, time)

    def get_constellation_lines(self) -> list[ConstellationLineSegment]:
        """Return the constellation stick-figure line segments.

        Owner:
            wayfindinglib.tasks.planning_tasks.sky_answers.get_constellation_lines

        Returns
        -------
        lines : `list` [`ConstellationLineSegment`]
            Every straight piece of every constellation's stick figure.
        """
        from wayfindinglib.tasks.planning_tasks.sky_answers import get_constellation_lines

        return get_constellation_lines(self._context)

    def get_panels(
        self,
        center: Target | StellarObject | SkyPosition,
        rows: int = 1,
        cols: int = 1,
        *,
        overlap_pct: float = 0.0,
        rotation_deg: float = 0.0,
        configuration: ObservatoryConfiguration | None = None,
    ) -> list[MosaicPanel]:
        """Work out the center of each panel of a mosaic grid.

        Nothing is stored. ``packages.create_mosaic`` adds the panels to the
        library.

        Owner:
            wayfindinglib.tasks.planning_tasks.sky_answers.get_panels

        Parameters
        ----------
        center : `Target`, `StellarObject` or `SkyPosition`
            The object or position at the grid center.
        rows : `int`, optional
            Number of panel rows. Defaults to 1.
        cols : `int`, optional
            Number of panel columns. Defaults to 1.
        overlap_pct : `float`, optional
            How much neighboring panels overlap, from 0 to under 100.
            Defaults to 0.
        rotation_deg : `float`, optional
            How far the camera is turned, in degrees east of north, as Ekos
            and KStars give it. At 0 the rows run along declination; at 90
            the two grid axes swap. Defaults to 0.
        configuration : `ObservatoryConfiguration`, optional
            The equipment whose field of view sets the spacing. The
            configured sensor and focal length when omitted.

        Returns
        -------
        panels : `list` [`MosaicPanel`]
            One panel per grid cell, row by row. Each carries the rotation.
        """
        from wayfindinglib.tasks.planning_tasks.sky_answers import get_panels

        return get_panels(self._context, center, rows, cols, overlap_pct, rotation_deg, configuration)
