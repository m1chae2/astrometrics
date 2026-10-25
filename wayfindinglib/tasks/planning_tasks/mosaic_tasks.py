"""Purpose: Work out mosaic panels, and add their targets and packages.

Description: A mosaic covers a region too large for one picture with a
grid of overlapping panels. `calculate_panels` works out each panel's
center. The grid is centered on the region; neighbouring panels are one
field of view apart, less the overlap. Along right ascension (RA) the step
is divided by cos(declination), because lines of equal RA draw together
toward the poles, so the same angle on the sky spans more RA there.

`create_mosaic` adds one library target per panel, copying the parent's
camera, telescope and field of view, and can record one
`ObservationPackage` per panel with a shared exposure recipe. From then on
each panel is an ordinary target with an ordinary package.

The field of view comes from the active `EquipmentConfiguration` when the
caller gives one. Otherwise it comes from the configuration's camera
sensor size and telescope focal length, with a 23.5 x 15.6 mm sensor and a
400 mm focal length used for any value that is missing.
"""

import math
import uuid
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

from astrometricslib import InvalidArgumentError, Target, parse_coordinate_string
from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration
from wayfindinglib.models.planning.framing import SkyCorner
from wayfindinglib.models.planning.mosaic import MosaicPanel, MosaicPlan
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    ObservationPackage,
)

DEFAULT_SENSOR_WIDTH_MM = 23.5
"""Sensor width used when the configuration gives none."""

DEFAULT_SENSOR_HEIGHT_MM = 15.6
"""Sensor height used when the configuration gives none."""

DEFAULT_FOCAL_LENGTH_MM = 400.0
"""Focal length used when the configuration gives none."""


def _configured_field_of_view(config: Any) -> tuple[float, float]:
    """Compute the field of view from the configured sensor and focal length.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    fov_deg : `tuple` [`float`, `float`]
        The (width, height) field of view, in degrees.
    """

    def positive_value(section: str, key: str, default: float) -> float:
        """Read a configured number, or use `default` if it is not positive.

        Parameters
        ----------
        section : `str`
            The configuration section.
        key : `str`
            The key inside the section.
        default : `float`
            The value to use when the setting is missing or not positive.

        Returns
        -------
        value : `float`
            The configured number, or `default`.
        """
        value = float(config.get_value(section, key) or 0.0)
        return value if value > 0 else default

    width_mm = positive_value("Observatory.Camera", "sensor_width_mm", DEFAULT_SENSOR_WIDTH_MM)
    height_mm = positive_value("Observatory.Camera", "sensor_height_mm", DEFAULT_SENSOR_HEIGHT_MM)
    focal_length_mm = positive_value("Observatory.Telescope", "focal_length_mm", DEFAULT_FOCAL_LENGTH_MM)
    return (
        2 * math.degrees(math.atan((width_mm / 2) / focal_length_mm)),
        2 * math.degrees(math.atan((height_mm / 2) / focal_length_mm)),
    )


def _grid_offset_to_sky_position(
    column_offset_deg: float,
    row_offset_deg: float,
    center_ra_deg: float,
    center_dec_deg: float,
    rotation_sin: float,
    rotation_cos: float,
) -> tuple[float, float]:
    """Turn a grid offset into a right ascension and declination.

    The grid is turned by the camera rotation (up is `rotation` degrees east
    of north, and a larger column points 90 degrees clockwise from up). The
    east offset is then divided by cos(declination) to become a right
    ascension step. This flat-sky step is the one the panel centers use, so
    corners and centers agree. It matches a true tangent-plane projection to
    first order, which is accurate for offsets of a few degrees.

    Parameters
    ----------
    column_offset_deg : `float`
        Offset along the grid columns, in degrees.
    row_offset_deg : `float`
        Offset along the grid rows, in degrees.
    center_ra_deg : `float`
        Right ascension of the grid center, in degrees.
    center_dec_deg : `float`
        Declination of the grid center, in degrees.
    rotation_sin : `float`
        Sine of the camera rotation.
    rotation_cos : `float`
        Cosine of the camera rotation.

    Returns
    -------
    ra_deg, dec_deg : `tuple` [`float`, `float`]
        The sky position, with right ascension in [0, 360).
    """
    ra_offset = column_offset_deg * rotation_cos + row_offset_deg * rotation_sin
    dec_offset = row_offset_deg * rotation_cos - column_offset_deg * rotation_sin
    dec_deg = center_dec_deg + dec_offset
    cos_dec = max(abs(math.cos(math.radians(dec_deg))), 1e-5)
    return (center_ra_deg + ra_offset / cos_dec) % 360.0, dec_deg


def calculate_panels(
    config: Any,
    center_ra: str,
    center_dec: str,
    rows: int,
    cols: int,
    overlap_percent: float,
    equipment: EquipmentConfiguration | None = None,
    rotation_deg: float = 0.0,
) -> list[MosaicPanel]:
    """Work out the center of every panel in a mosaic grid.

    With a rotation, the whole grid is turned about its center the way the
    camera is turned. The camera's up direction (increasing row) points
    `rotation_deg` east of north, and its columns follow. At 0 degrees the
    rows run along declination and the columns along right ascension. At 90
    degrees the rows run east and the columns run south, so the two grid
    axes swap.

    Parameters
    ----------
    config : `AppConfiguration`
        Supplies the field of view when `equipment` is not given.
    center_ra : `str`
        Right ascension of the grid center, as text (hours) or degrees.
    center_dec : `str`
        Declination of the grid center, as text or degrees.
    rows : `int`
        Number of panel rows.
    cols : `int`
        Number of panel columns.
    overlap_percent : `float`
        How much neighbouring panels overlap, from 0 to just under 100.
    equipment : `EquipmentConfiguration`, optional
        The telescope and camera; their field of view sets the spacing.
    rotation_deg : `float`, optional
        How far the camera is turned, in degrees east of north, the way
        Ekos and KStars give it. Defaults to 0.

    Returns
    -------
    panels : `list` [`MosaicPanel`]
        One panel per grid cell, row by row.

    Raises
    ------
    InvalidArgumentError
        If the grid size, overlap or rotation is out of range.
    """
    if not math.isfinite(rotation_deg):
        raise InvalidArgumentError("rotation_deg must be a finite number of degrees.")
    if rows < 1 or cols < 1:
        raise InvalidArgumentError("rows and cols must each be at least 1.")
    if not 0.0 <= overlap_percent < 100.0:
        raise InvalidArgumentError("overlap_percent must be from 0 to under 100.")
    if equipment is not None:
        fov_width_deg, fov_height_deg = equipment.fov_width_deg, equipment.fov_height_deg
    else:
        fov_width_deg, fov_height_deg = _configured_field_of_view(config)
    overlap_fraction = overlap_percent / 100.0
    step_width_deg = fov_width_deg * (1.0 - overlap_fraction)
    step_height_deg = fov_height_deg * (1.0 - overlap_fraction)
    ra_offsets = [(column - (cols - 1) / 2.0) * step_width_deg for column in range(cols)]
    dec_offsets = [(row - (rows - 1) / 2.0) * step_height_deg for row in range(rows)]

    center_ra_deg = parse_coordinate_string(center_ra, is_ra=True)
    center_dec_deg = parse_coordinate_string(center_dec, is_ra=False)

    rotation_sin = math.sin(math.radians(rotation_deg))
    rotation_cos = math.cos(math.radians(rotation_deg))
    panels = []
    for row, row_offset_deg in enumerate(dec_offsets):
        for column, column_offset_deg in enumerate(ra_offsets):
            # Up (a larger row) points rotation_deg east of north; a larger
            # column points a further 90 degrees clockwise. At 0 degrees
            # the offsets are unchanged.
            panel_ra_deg, panel_dec_deg = _grid_offset_to_sky_position(
                column_offset_deg, row_offset_deg, center_ra_deg, center_dec_deg, rotation_sin, rotation_cos
            )
            # The four corners of the field of view, lower left first and
            # then counterclockwise in the camera image.
            corners = [
                SkyCorner(
                    ra_deg=corner_ra_deg,
                    dec_deg=corner_dec_deg,
                )
                for corner_ra_deg, corner_dec_deg in (
                    _grid_offset_to_sky_position(
                        column_offset_deg + column_sign * fov_width_deg / 2.0,
                        row_offset_deg + row_sign * fov_height_deg / 2.0,
                        center_ra_deg,
                        center_dec_deg,
                        rotation_sin,
                        rotation_cos,
                    )
                    for column_sign, row_sign in ((-1, -1), (1, -1), (1, 1), (-1, 1))
                )
            ]
            position = SkyCoord(ra=panel_ra_deg * u.deg, dec=panel_dec_deg * u.deg)
            panels.append(
                MosaicPanel(
                    row=row,
                    col=column,
                    ra_str=position.ra.to_string(unit=u.hourangle, sep=" ", precision=1, pad=True),
                    dec_str=position.dec.to_string(
                        unit=u.deg, sep=" ", precision=1, pad=True, alwayssign=True
                    ),
                    ra_deg=panel_ra_deg,
                    dec_deg=panel_dec_deg,
                    panel_id=f"P{row + 1}_{column + 1}",
                    rotation_deg=rotation_deg,
                    corners=corners,
                )
            )
    return panels


def create_mosaic(
    astrometrics: Any,
    parent: Target,
    panels: list[MosaicPanel],
    exposure_requests: list[ExposureRequest] | None,
    dither_config: DitherConfig | None,
    packages: bool,
) -> MosaicPlan:
    """Add one library target per panel, and optionally one package each.

    Parameters
    ----------
    astrometrics : `astrometricslib.Astrometrics`
        The science library; its target catalog gets the panel targets.
    parent : `Target`
        The target the mosaic covers.
    panels : `list` [`MosaicPanel`]
        The panels, as `calculate_panels` returns them.
    exposure_requests : `list` [`ExposureRequest`] or `None`
        The exposure recipe every panel's package shares.
    dither_config : `DitherConfig` or `None`
        The dithering every panel's package shares.
    packages : `bool`
        Whether to build one package per panel. The caller records them.

    Returns
    -------
    plan : `MosaicPlan`
        The panel target ids and, when asked for, the packages.
    """
    plan = MosaicPlan(parent_target_id=parent.id, panels=list(panels))
    for panel in panels:
        panel_target_id = f"{parent.id}_{panel.panel_id}"
        panel_target = Target(
            id=panel_target_id, commonName=panel_target_id, ra=panel.ra_str, dec=panel.dec_str
        )
        panel_target.main_camera = parent.main_camera
        panel_target.main_scope = parent.main_scope
        panel_target.field_of_view = parent.field_of_view
        astrometrics.targets.add(panel_target)
        plan.target_ids.append(panel_target_id)
        if packages:
            plan.packages.append(
                ObservationPackage(
                    id=str(uuid.uuid4()),
                    name=f"{parent.id} panel {panel.row + 1},{panel.col + 1}",
                    target_id=panel_target_id,
                    exposure_requests=list(exposure_requests or []),
                    dither_config=dither_config,
                )
            )
    astrometrics.targets.save()
    return plan
