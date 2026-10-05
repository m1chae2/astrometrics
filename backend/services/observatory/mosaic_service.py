"""Purpose: Coordinate math and target generation for mosaic imaging plans.

REQ: BKD-4.3
"""

from typing import Any


class MosaicService:
    """Calculate mosaic panel coordinates and generate their targets."""

    def __init__(self, wayfinder):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
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

    def create_mosaic_targets_rpc(
        self, parent_target_id: str, grid: dict, panels: list, image_config: dict, dither_config: dict
    ) -> list[dict[str, Any]]:
        """RPC wrapper for mosaic creation using standard param names.

        Returns
        -------
        plan_items : `list` [`dict`]
            One plan item per panel, as returned by
            `ObservationPlanning.create_mosaic_targets`.
        """
        return self.wayfinder.planning.create_mosaic_targets(
            parent_target_id=parent_target_id,
            grid_config=grid,
            panels=panels,
            image_config=image_config,
            dither_config=dither_config,
        )
