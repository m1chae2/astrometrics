"""Purpose: Coordinate math and target generation for mosaic imaging plans.

REQ: BKD-4.3
"""

import time
import uuid
from typing import Any


class MosaicService:
    """Calculate mosaic panel coordinates and generate their targets."""

    def __init__(self, target_manager, wayfinder):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.target_manager = target_manager
        self.wayfinder = wayfinder

    def preview_mosaic(self, center_ra: float, center_dec: float, grid: dict) -> list[dict[str, Any]]:
        """RPC wrapper to map grid parameters to calculate_panels.

        Returns
        -------
        panels : `list` [`dict`]
            The RA/DEC coordinates of every panel in the mosaic grid.
        """
        rows = grid.get("rows", 1)
        cols = grid.get("cols", 1)
        overlap = grid.get("overlap", 0.0)
        return self.calculate_panels(
            center_ra=str(center_ra),
            center_dec=str(center_dec),
            rows=rows,
            cols=cols,
            overlap_percent=float(overlap),
        )

    def calculate_panels(
        self, center_ra: str, center_dec: str, rows: int, cols: int, overlap_percent: float
    ) -> list[dict[str, Any]]:
        """Calculate the RA/DEC coordinates for a mosaic grid.

        Delegates to the canonical wayfindinglib implementation rather than
        maintaining an independent copy of the panel-offset math.

        Returns
        -------
        panels : `list` [`dict`]
            The RA/DEC coordinates of every panel in the mosaic grid.
        """
        return self.wayfinder.planning.calculate_panels(
            center_ra=center_ra, center_dec=center_dec, rows=rows, cols=cols, overlap_percent=overlap_percent
        )

    def create_mosaic_targets(
        self,
        parent_target_id: str,
        grid_config: dict[str, Any],
        panels: list[dict[str, Any]],
        image_config: dict[str, Any],
        dither_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Create panel targets and return PlanItems.

        Returns
        -------
        plan_items : `list` [`dict`]
            One plan item per panel, describing the imaging job
            (type, count, exposure, filter, delay, coordinates, and
            optional dither settings) to run against that panel.

        Raises
        ------
        ValueError
            If `parent_target_id` does not resolve to an existing
            target via `target_manager.get_target`.
        """
        from astrometricslib import MosaicInfo, Target

        parent = self.target_manager.get_target(parent_target_id)
        if not parent:
            raise ValueError(f"Parent target {parent_target_id} not found")

        group_id = str(uuid.uuid4())
        group_name = f"{grid_config.get('rows')}x{grid_config.get('cols')} Mosaic"
        panel_target_ids = []
        plan_items = []

        for p in panels:
            panel_suffix = f"_P{p['row'] + 1}_{p['col'] + 1}"
            new_name = (parent.common_name or parent.id) + panel_suffix
            new_target = Target(
                id=new_name,
                commonName=new_name,
                ra=p["ra_str"],
                dec=p["dec_str"],
                parentGroupId=group_id,
                panelName=p["panel_id"],
            )
            new_target.main_camera = parent.main_camera
            new_target.main_scope = parent.main_scope
            new_target.field_of_view = parent.field_of_view

            self.target_manager.create_target(new_target)
            panel_target_ids.append(new_name)

            plan_items.append({
                "id": str(uuid.uuid4()),
                "type": image_config["type"],
                "count": image_config["count"],
                "exposure": image_config["exposure"],
                "filter": image_config["filter"],
                "delay": image_config.get("delay", 0),
                "coordinates": {"ra": p["ra_deg"], "dec": p["dec_deg"]},
                "dither": dither_config if dither_config.get("enabled") else None,
                "name": p["panel_id"],
            })

        parent.mosaic_groups.append(
            MosaicInfo(group_id=group_id, name=group_name, created_at=time.time(), panels=panel_target_ids)
        )
        return plan_items

    def create_mosaic_targets_rpc(
        self, parent_target_id: str, grid: dict, panels: list, image_config: dict, dither_config: dict
    ) -> list[dict[str, Any]]:
        """RPC wrapper for mosaic creation using standard param names.

        Returns
        -------
        plan_items : `list` [`dict`]
            One plan item per panel, as returned by
            `create_mosaic_targets`.
        """
        return self.create_mosaic_targets(
            parent_target_id=parent_target_id,
            grid_config=grid,
            panels=panels,
            image_config=image_config,
            dither_config=dither_config,
        )
