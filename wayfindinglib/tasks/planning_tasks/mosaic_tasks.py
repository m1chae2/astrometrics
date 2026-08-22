"""Purpose: Mosaic Panel Set Generation.

Description: Expands one `MosaicGridConfig` plus a shared exposure
recipe into one `ObservationPackage` per `MosaicPanel`: for each panel,
creates a panel sub-target through astrometricslib's public
high-level interface, then constructs a package pointing at that
sub-target with the shared recipe
(`Wayfinding_Library_Architecture.md` §2.3.2). Each generated
package is independent from that point on -- placement, quality
advisory, and calibration advisory all treat it like any other package.

The panel-offset math (gnomonic RA compression by 1/cos(dec)) is
carried forward from the deprecated `observationlib.planning_operations
.calculate_panels`; field-of-view is read from the new
`EquipmentConfiguration`'s already-computed properties rather than raw
config keys, since Planning takes the active equipment as an argument
(hardware-free) rather than reading it itself.
"""

import math
import uuid

import astropy.units as u
from astropy.coordinates import SkyCoord

from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration
from wayfindinglib.models.planning.mosaic import MosaicGridConfig
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    ObservationPackage,
)


def _panel_coordinates(
    center_ra_deg: float,
    center_dec_deg: float,
    grid_config: MosaicGridConfig,
    equipment: EquipmentConfiguration,
) -> list[tuple[int, int, float, float]]:
    """Compute (row, col, ra_deg, dec_deg) for every panel in the grid.

    Returns
    -------
    panels : `list` [`tuple` [`int`, `int`, `float`, `float`]]
        Each panel's `(row, col, ra_deg, dec_deg)`.
    """
    overlap_fraction = grid_config.overlap_percent / 100.0
    step_w_deg = equipment.fov_width_deg * (1.0 - overlap_fraction)
    step_h_deg = equipment.fov_height_deg * (1.0 - overlap_fraction)

    ra_offsets = [(c - (grid_config.cols - 1) / 2.0) * step_w_deg for c in range(grid_config.cols)]
    dec_offsets = [(r - (grid_config.rows - 1) / 2.0) * step_h_deg for r in range(grid_config.rows)]

    panels = []
    for row, dec_off in enumerate(dec_offsets):
        for col, ra_off in enumerate(ra_offsets):
            panel_dec_deg = center_dec_deg + dec_off
            cos_dec = math.cos(math.radians(panel_dec_deg))
            if abs(cos_dec) < 1e-5:
                cos_dec = 1e-5
            panel_ra_deg = (center_ra_deg + ra_off / cos_dec) % 360.0
            panels.append((row, col, panel_ra_deg, panel_dec_deg))
    return panels


def generate_mosaic_packages(
    astrometrics,  # ruff: ignore[missing-type-function-argument]
    parent_target_id: str,
    grid_config: MosaicGridConfig,
    exposure_requests: list[ExposureRequest],
    equipment: EquipmentConfiguration,
    dither_config: DitherConfig | None = None,
) -> list[ObservationPackage]:
    """Expand one multi-panel imaging request into a sibling set of packages.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's public high-level interface
        (`astrometricslib.Astrometrics`), used to resolve the parent
        target and create one sub-target per panel.
    parent_target_id : `str`
        The already-resolved target the mosaic tiles around.
    grid_config : `MosaicGridConfig`
        The requested tiling.
    exposure_requests : `list` [`ExposureRequest`]
        The shared exposure recipe applied to every panel.
    equipment : `EquipmentConfiguration`
        The active telescope/camera pairing, supplying the field of
        view each panel's spacing is computed from.
    dither_config : `DitherConfig`, optional
        The shared dithering cadence applied to every panel.

    Returns
    -------
    packages : `list` [`ObservationPackage`]
        One independently placeable package per panel.

    Raises
    ------
    ValueError
        Raised if `parent_target_id` does not resolve to an existing
        target.
    """
    parent = astrometrics.targets.get(parent_target_id)
    if not parent:
        raise ValueError(f"Parent target {parent_target_id} not found")

    center_ra_deg = _parse_ra(parent.ra)
    center_dec_deg = _parse_dec(parent.dec)

    packages = []
    for row, col, panel_ra_deg, panel_dec_deg in _panel_coordinates(
        center_ra_deg, center_dec_deg, grid_config, equipment
    ):
        panel_target_id = f"{parent_target_id}_P{row + 1}_{col + 1}"
        panel_target = astrometrics.targets.create(panel_target_id)
        coord = SkyCoord(ra=panel_ra_deg * u.deg, dec=panel_dec_deg * u.deg)
        panel_target.ra = coord.ra.to_string(unit=u.hourangle, sep=" ", precision=1, pad=True)
        panel_target.dec = coord.dec.to_string(unit=u.deg, sep=" ", precision=1, pad=True, alwayssign=True)
        astrometrics.targets.add(panel_target)

        packages.append(
            ObservationPackage(
                id=str(uuid.uuid4()),
                name=f"{parent_target_id} panel {row + 1},{col + 1}",
                target_id=panel_target_id,
                exposure_requests=list(exposure_requests),
                dither_config=dither_config,
            )
        )

    astrometrics.targets.save()
    return packages


def _parse_ra(ra: str) -> float:
    """Parse a target's RA field into decimal degrees.

    Returns
    -------
    ra_deg : `float`
        The parsed RA, in decimal degrees.
    """
    from astrometricslib import parse_coordinate_string

    return parse_coordinate_string(ra, is_ra=True)


def _parse_dec(dec: str) -> float:
    """Parse a target's Dec field into decimal degrees.

    Returns
    -------
    dec_deg : `float`
        The parsed Dec, in decimal degrees.
    """
    from astrometricslib import parse_coordinate_string

    return parse_coordinate_string(dec, is_ra=False)
