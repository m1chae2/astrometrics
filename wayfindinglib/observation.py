"""Astronomical planning, target visibility, and rise/set estimation.

Models: SequenceItem, SequencePlan, CalibrationEntry, CalibrationStats,
MosaicPanel, Observation, SessionPlanner, PlanningAPI # REQ: BKD-4:
Astronomical Calculations
"""

import logging
from typing import Any

import astropy.units as u
from astropy.coordinates import EarthLocation
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class SequenceItem(BaseModel):
    """A single instruction block inside an imaging sequence plan."""

    model_config = ConfigDict(populate_by_name=True)
    count: int = Field(..., alias="count")
    exposure: float = Field(..., alias="exposure")
    filter: str = Field(..., alias="filter")
    duration: float = Field(..., alias="duration")


class SequencePlan(BaseModel):
    """A full structured session queue of planned exposure sequences."""

    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(..., alias="id")
    target_name: str = Field(..., alias="target_name")
    items: list[SequenceItem] = Field(default_factory=list, alias="items")
    total_duration: float = Field(default=0.0, alias="total_duration")
    created_at: str = Field(..., alias="created_at")
    status: str = Field(default="planned", alias="status")


class CalibrationEntry(BaseModel):
    """Metadata for a single registered calibration frame."""

    model_config = ConfigDict(populate_by_name=True)
    camera: str = Field(..., alias="camera")
    iso: str = Field(..., alias="iso")
    exposure: float | None = Field(default=None, alias="exposure")
    filter: str | None = Field(default=None, alias="filter")
    count: int = Field(..., alias="count")


class CalibrationStats(BaseModel):
    """Registered dark, flat, and bias frame statistics for the library."""

    model_config = ConfigDict(populate_by_name=True)
    darks: list[CalibrationEntry] = Field(default_factory=list, alias="darks")
    biases: list[CalibrationEntry] = Field(default_factory=list, alias="biases")
    flats: list[CalibrationEntry] = Field(default_factory=list, alias="flats")


class MosaicPanel(BaseModel):
    """Row/column coordinates for a single pane in a mosaic layout."""

    model_config = ConfigDict(populate_by_name=True)
    row: int = Field(..., alias="row")
    col: int = Field(..., alias="col")
    ra_str: str = Field(..., alias="ra_str")
    dec_str: str = Field(..., alias="dec_str")
    ra_deg: float = Field(..., alias="ra_deg")
    dec_deg: float = Field(..., alias="dec_deg")
    panel_id: str = Field(..., alias="panel_id")


class SessionPlanner:
    """Compute mosaic panels and design sequence session queues."""

    def __init__(self, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the SessionPlanner with application configuration."""
        self._config = config

    def create_sequence_plan(self, target_name: str, plan_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Delegate sequence plan creation to observationlib.

        Returns
        -------
        sequence_plan : `dict`
            The structured sequence plan for the target.
        """
        from wayfindinglib.observationlib import planning_operations

        return planning_operations.create_sequence_plan(self, target_name, plan_items)

    def calculate_panels(
        self, center_ra: str, center_dec: str, rows: int, cols: int, overlap_percent: float
    ) -> list[dict[str, Any]]:
        """Delegate panel calculation to observationlib.

        Returns
        -------
        panels : `list` [`dict`]
            RA/DEC coordinate offsets for each mosaic panel.
        """
        from wayfindinglib.observationlib import planning_operations

        return planning_operations.calculate_panels(
            self._config, center_ra, center_dec, rows, cols, overlap_percent
        )

    def create_mosaic_targets(
        self,
        parent_target_id: str,
        grid_config: dict[str, Any],
        panels: list[dict[str, Any]],
        image_config: dict[str, Any],
        dither_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Delegate mosaic targets creation to observationlib.

        Returns
        -------
        target_records : `list` [`dict`]
            The generated target records for the mosaic panels.
        """
        from wayfindinglib.observationlib import planning_operations

        return planning_operations.create_mosaic_targets(
            self, parent_target_id, grid_config, panels, image_config, dither_config
        )


class Observation:
    """Session planning, visibility, sequence, and mosaic calculation."""

    def __init__(self, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the Observation planner astrometrics."""
        self._config = config
        self._planner = SessionPlanner(config)
        self.location = EarthLocation(lat=39.7392 * u.deg, lon=-104.9903 * u.deg, height=1600 * u.m)

    def get_target_status(self, target_id: str) -> dict[str, Any] | None:
        """Delegate get_target_status to observationlib.

        Returns
        -------
        status : `dict` or `None`
            The target's visibility/status fields, or `None` if the
            target could not be resolved.
        """
        from wayfindinglib.observationlib import visibility_calculations

        return visibility_calculations.get_target_status(self, target_id)

    def get_visible_targets(self) -> list[dict[str, Any]]:
        """Delegate get_visible_targets to observationlib.

        Returns
        -------
        targets : `list` [`dict`]
            Status/visibility fields for each currently visible target.
        """
        from wayfindinglib.observationlib import visibility_calculations

        return visibility_calculations.get_visible_targets(self)

    def create_sequence_plan(self, target_name: str, plan_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a structured sequence plan.

        Returns
        -------
        sequence_plan : `dict`
            The structured sequence plan for the target.
        """
        return self._planner.create_sequence_plan(target_name, plan_items)

    def calculate_panels(
        self, center_ra: str, center_dec: str, rows: int, cols: int, overlap_percent: float
    ) -> list[dict[str, Any]]:
        """Calculate RA/DEC coordinate offsets for a multi-panel mosaic.

        Returns
        -------
        panels : `list` [`dict`]
            RA/DEC coordinate offsets for each mosaic panel.
        """
        return self._planner.calculate_panels(center_ra, center_dec, rows, cols, overlap_percent)

    def create_mosaic_targets(
        self,
        parent_target_id: str,
        grid_config: dict[str, Any],
        panels: list[dict[str, Any]],
        image_config: dict[str, Any],
        dither_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate target records for mosaic panels linked to parent.

        Returns
        -------
        target_records : `list` [`dict`]
            The generated target records for the mosaic panels.
        """
        return self._planner.create_mosaic_targets(
            parent_target_id, grid_config, panels, image_config, dither_config
        )
