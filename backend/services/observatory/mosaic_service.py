"""Purpose: Serve the Mosaic Planner's preview and create requests.

Description: The UI previews a mosaic grid, then creates it. Both ask
``Wayfinder.planning``: ``sky.get_panels`` works out the panel centers and
``packages.create_mosaic`` adds one library target per panel. The create
reply is one item per panel in the shape the app's sequencer queue takes,
so the UI can queue the panels straight away.
"""

import uuid
from typing import Any

from pydantic import ValidationError

from astrometricslib import SkyPosition
from astrometricslib.exceptions import InvalidArgumentError
from backend.services.subject_resolver import SubjectResolver
from wayfindinglib import MosaicPanel


class MosaicService:
    """Preview mosaic grids and add their panel targets."""

    def __init__(self, wayfinder: Any) -> None:
        """Keep the Wayfinder whose planning API does the work.

        Parameters
        ----------
        wayfinder : `wayfindinglib.Wayfinder`
            The shared Wayfinder.
        """
        self.wayfinder = wayfinder

    def preview_mosaic(
        self, center_ra: float, center_dec: float, grid: dict, rotation_deg: float = 0.0
    ) -> list[Any]:
        """Work out the panel centers of a grid around a point.

        Parameters
        ----------
        center_ra : `float`
            Right ascension of the grid center, in degrees.
        center_dec : `float`
            Declination of the grid center, in degrees.
        grid : `dict`
            ``rows``, ``cols`` and ``overlap`` (percent); each defaults
            to 1, 1 and 0.
        rotation_deg : `float`, optional
            How far the camera is turned, in degrees east of north, as Ekos
            and KStars give it. The library defines the convention.

        Returns
        -------
        panels : `list` [`MosaicPanel`]
            One panel per grid cell.
        """
        center = SkyPosition(ra_deg=float(center_ra) % 360.0, dec_deg=float(center_dec))
        return self.wayfinder.planning.sky.get_panels(
            center,
            grid.get("rows", 1),
            grid.get("cols", 1),
            overlap_pct=float(grid.get("overlap", 0.0)),
            rotation_deg=float(rotation_deg),
        )

    def create_mosaic(
        self, parent_target_id: str, grid: dict, panels: list, image_config: dict, dither_config: dict
    ) -> list[dict[str, Any]]:
        """Add one library target per panel and describe each for the queue.

        Parameters
        ----------
        parent_target_id : `str`
            The target the mosaic covers.
        grid : `dict`
            The grid the panels came from. Not used; the panels say it all.
        panels : `list` [`dict`]
            The panels, as `preview_mosaic` returned them.
        image_config : `dict`
            ``type``, ``count``, ``exposure``, ``filter`` and an optional
            ``delay`` for every panel.
        dither_config : `dict`
            The dithering for every panel; kept only when ``enabled``.

        Returns
        -------
        plan_items : `list` [`dict`]
            One sequencer item per panel, with the panel's center and name.

        Raises
        ------
        InvalidArgumentError
            If a panel is not in the shape the preview gives.
        """
        parent = SubjectResolver(self.wayfinder.astrometrics).target(parent_target_id)
        try:
            panel_models = [MosaicPanel.model_validate(panel) for panel in panels]
        except ValidationError as error:
            raise InvalidArgumentError(
                "Each panel needs row, col, ra_str, dec_str, ra_deg, dec_deg and panel_id, as the preview "
                "gives them.",
                details={"error": str(error)},
            ) from error
        plan = self.wayfinder.planning.packages.create_mosaic(parent, panel_models)
        dither = dither_config if dither_config.get("enabled") else None
        return [
            {
                "id": str(uuid.uuid4()),
                "type": image_config["type"],
                "count": image_config["count"],
                "exposure": image_config["exposure"],
                "filter": image_config["filter"],
                "delay": image_config.get("delay", 0),
                "coordinates": {"ra": panel.ra_deg, "dec": panel.dec_deg},
                "dither": dither,
                "name": panel.panel_id,
            }
            for panel in plan.panels
        ]
