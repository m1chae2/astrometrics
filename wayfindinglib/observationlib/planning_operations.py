"""Astronomical planning, sequence plans, and mosaic panel calculations.

Provides high-level sequence item and plan structures, calculates
panels for mosaic layouts, and generates dither configurations.
"""

import logging
import math
import time
import uuid
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

from astrometricslib import parse_coordinate_string

logger = logging.getLogger(__name__)


def create_sequence_plan(planner, target_name: str, plan_items: list[dict[str, Any]]) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Generate a structured sequence plan from configuration inputs.

    Returns
    -------
    sequence_plan : `dict`
        The structured plan with an id, formatted items, total
        duration, creation timestamp, and status.
    """
    logger.info(f"Creating sequence plan for {target_name} with {len(plan_items)} items")

    total_duration = 0.0
    formatted_items = []

    for item in plan_items:
        count = int(item.get("count", 0))
        exposure = float(item.get("exposure", 0.0))
        filter_name = str(item.get("filter", "L"))

        duration = count * exposure
        total_duration += duration

        formatted_items.append({
            "count": count,
            "exposure": exposure,
            "filter": filter_name,
            "duration": duration,
        })

    sequence_id = str(uuid.uuid4())

    return {
        "id": sequence_id,
        "target_name": target_name,
        "items": formatted_items,
        "total_duration": total_duration,
        "created_at": str(time.time()),
        "status": "planned",
    }


def calculate_panels(
    config_service,  # ruff: ignore[missing-type-function-argument]
    center_ra: str,
    center_dec: str,
    rows: int,
    cols: int,
    overlap_percent: float,
) -> list[dict[str, Any]]:
    """Calculate RA/DEC coordinate offsets for a multi-panel mosaic grid.

    Returns
    -------
    panels : `list` [`dict`]
        Per-panel row/col indices, formatted and decimal-degree
        RA/Dec, and a panel id.
    """
    fov_w_deg, fov_h_deg = _get_fov_from_config(config_service)
    overlap_fraction = overlap_percent / 100.0
    step_w = fov_w_deg * (1.0 - overlap_fraction)
    step_h = fov_h_deg * (1.0 - overlap_fraction)

    ra_offsets = [(c - (cols - 1) / 2.0) * step_w for c in range(cols)]
    dec_offsets = [(r - (rows - 1) / 2.0) * step_h for r in range(rows)]

    # Sanitize via the canonical parser (handles unit-marker-less sexagesimal
    # strings, e.g. plate-solved/SIMBAD-resolved coordinates, and the pydantic
    # placeholder default) rather than passing the raw string straight into
    # SkyCoord, which previously crashed on both.
    center_ra_deg = parse_coordinate_string(center_ra, is_ra=True)
    center_dec_deg = parse_coordinate_string(center_dec, is_ra=False)

    panels = []
    for r_idx, dec_off in enumerate(dec_offsets):
        for c_idx, ra_off in enumerate(ra_offsets):
            new_dec = center_dec_deg + dec_off
            cos_dec = math.cos(math.radians(new_dec))
            if abs(cos_dec) < 1e-5:
                cos_dec = 1e-5

            ra_adjust = ra_off / cos_dec
            new_ra = (center_ra_deg + ra_adjust) % 360.0

            coord = SkyCoord(ra=new_ra * u.deg, dec=new_dec * u.deg)
            ra_str = coord.ra.to_string(unit=u.hourangle, sep=" ", precision=1, pad=True)
            dec_str = coord.dec.to_string(unit=u.deg, sep=" ", precision=1, pad=True, alwayssign=True)

            panels.append({
                "row": r_idx,
                "col": c_idx,
                "ra_str": ra_str,
                "dec_str": dec_str,
                "ra_deg": new_ra,
                "dec_deg": new_dec,
                "panel_id": f"P{r_idx + 1}_{c_idx + 1}",
            })
    return panels


def create_mosaic_targets(
    planner,  # ruff: ignore[missing-type-function-argument]
    parent_target_id: str,
    grid_config: dict[str, Any],
    panels: list[dict[str, Any]],
    image_config: dict[str, Any],
    dither_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate target records for each mosaic panel, linked to the parent.

    Creates one Target per panel in `panels`, positioned at that
    panel's coordinates and tagged with the shared mosaic group id,
    then appends a MosaicInfo record to the parent target.

    Returns
    -------
    plan_items : `list` [`dict`]
        The generated sequence plan items, one per panel.

    Raises
    ------
    ValueError
        If ``parent_target_id`` does not resolve to an existing
        target.
    """
    from astrometricslib import Astrometrics, MosaicInfo, Target

    astrometrics = Astrometrics(planner._config)
    parent = astrometrics.targets.get(parent_target_id)
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

        astrometrics.targets.add(new_target)
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
    astrometrics.targets.save()
    return plan_items


def _get_fov_from_config(config_service) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
    """Calculate field of view from configured sensor size and focal length.

    Returns
    -------
    fov_deg : `tuple` [`float`, `float`]
        The (width, height) field of view, in degrees.
    """
    sw = float(config_service.get_value("Camera", "sensor_width_mm") or 23.5)
    sh = float(config_service.get_value("Camera", "sensor_height_mm") or 15.6)
    fl = float(config_service.get_value("Telescope", "focal_length_mm") or 400.0)
    return 2 * math.degrees(math.atan((sw / 2) / fl)), 2 * math.degrees(math.atan((sh / 2) / fl))
