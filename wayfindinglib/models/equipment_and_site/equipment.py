"""Purpose: Equipment Domain Models.

Description: Telescope and camera specifications, held in a catalog of
which more than one may be configured with exactly one of each active.
Per `Wayfinding_Library_Architecture.md` §2.2.2: a telescope's optics and
its mount's safe pointing envelope are configured together, changed
together, and meaningless apart, so `Telescope` is one flat record
rather than separately swappable optics and mount components.

`meridian_flip_delay_min` is derived from `flip_hour_angle_deg` rather
than independently stored, per `Wayfinding_Library_Architecture.md`
§2.2.2: both describe the same physical threshold, and Phase 4 executing
flips rather than merely reporting them means the two can no longer be
allowed to disagree.

`EquipmentConfiguration`'s plate-scale and field-of-view formulas are
carried forward unchanged from the deprecated
`observatorylib.equipment_configuration.EquipmentConfiguration`, per
`Wayfinding_Library_Architecture.md` §2.2.5's verification requirement
that this arithmetic match the prior implementation exactly.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLATE_SCALE_CONSTANT = 206.265
"""Numerator in the standard plate-scale formula (radians x arcsec/rad / 1000).

plate_scale (arcsec/px) = 206.265 x pixel_size_um / focal_length_mm
"""

SIDEREAL_DEGREES_PER_HOUR = 15.0
"""Earth's sidereal rotation rate, in degrees of hour angle per hour."""


class Telescope(BaseModel):
    """A physical imaging rig's optics and safe pointing envelope.

    More than one may be configured in an `EquipmentCatalog`; exactly
    one is active. `flip_hour_angle_deg` is the single stored
    meridian-flip threshold; `meridian_flip_delay_min` is derived from
    it (Equation 1, `Wayfinding_Library_Architecture.md` §2.2.2).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    focal_length_mm: float = Field(..., gt=0.0, description="Effective focal length in millimetres.")
    focal_ratio: float = Field(default=0.0, ge=0.0, description="Focal ratio (f/number).")
    altitude_limits_enabled: bool = Field(default=True)
    min_altitude_deg: float = Field(default=0.0, ge=-90.0, le=90.0)
    max_altitude_deg: float = Field(default=90.0, ge=-90.0, le=90.0)
    hour_angle_limits_enabled: bool = Field(default=False)
    max_hour_angle_hours: float = Field(default=2.0, gt=0.0, le=12.0)
    flip_hour_angle_deg: float = Field(
        default=1.0,
        gt=0.0,
        description="Hour angle past transit at which a meridian flip is triggered, in degrees.",
    )

    @model_validator(mode="after")
    def _check_altitude_envelope_ordering(self) -> Telescope:
        """Verify the configured altitude envelope is non-empty.

        Returns
        -------
        telescope : `Telescope`
            This telescope, unchanged, once validated.

        Raises
        ------
        ValueError
            Raised if `min_altitude_deg` exceeds `max_altitude_deg`.
        """
        if self.min_altitude_deg > self.max_altitude_deg:
            raise ValueError(
                f"min_altitude_deg ({self.min_altitude_deg}) exceeds "
                f"max_altitude_deg ({self.max_altitude_deg})"
            )
        return self

    @property
    def meridian_flip_delay_min(self) -> float:
        """Meridian-flip trigger, in minutes past transit.

        Derived from `flip_hour_angle_deg` at the sidereal rate
        (Equation 1, `Wayfinding_Library_Architecture.md` §2.2.2):
        minutes = (degrees / 15) x 60.
        """
        return (self.flip_hour_angle_deg / SIDEREAL_DEGREES_PER_HOUR) * 60.0


class Camera(BaseModel):
    """A physical sensor's imaging geometry and thermal envelope.

    More than one may be configured in an `EquipmentCatalog`; exactly
    one is active.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    pixel_size_um: float = Field(..., gt=0.0, description="Pixel pitch in micrometres.")
    sensor_width_px: int = Field(..., gt=0, description="Sensor width in pixels.")
    sensor_height_px: int = Field(..., gt=0, description="Sensor height in pixels.")
    min_cooling_temp_c: float = Field(default=-30.0, description="Coldest temperature the sensor supports.")
    max_cooling_ramp_c_per_min: float = Field(
        default=2.0, gt=0.0, description="Maximum safe cooling/warming rate."
    )


class CoolingPolicy(BaseModel):
    """Target temperature and ramp behavior for one cooling session."""

    model_config = ConfigDict(populate_by_name=True)

    target_temp_c: float = Field(default=-10.0)
    ramp_c_per_min: float = Field(default=2.0, gt=0.0)
    settle_tolerance_c: float = Field(default=0.5, gt=0.0)
    settle_timeout_sec: int = Field(default=900, gt=0)


class EquipmentCatalog(BaseModel):
    """The configured set of telescopes and cameras, and which is active.

    Reading this catalog is a Foundation concern, since both Observation
    Planning and Observatory Control need the active specifications;
    changing which entry is active is a Control operation
    (`Wayfinding_Library_Architecture.md` §2.2.2, §2.5.2).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    telescopes: list[Telescope] = Field(default_factory=list)
    cameras: list[Camera] = Field(default_factory=list)
    active_telescope_id: str | None = Field(default=None)
    active_camera_id: str | None = Field(default=None)

    @model_validator(mode="after")
    def _check_active_ids_resolve(self) -> EquipmentCatalog:
        """Verify the active identifiers, when set, name a configured entry.

        Returns
        -------
        catalog : `EquipmentCatalog`
            This catalog, unchanged, once validated.

        Raises
        ------
        ValueError
            Raised if `active_telescope_id` or `active_camera_id` names an
            entry not present in `telescopes`/`cameras`.
        """
        if self.active_telescope_id is not None:
            if not any(t.id == self.active_telescope_id for t in self.telescopes):
                raise ValueError(f"active_telescope_id {self.active_telescope_id!r} is not in telescopes")
        if self.active_camera_id is not None:
            if not any(c.id == self.active_camera_id for c in self.cameras):
                raise ValueError(f"active_camera_id {self.active_camera_id!r} is not in cameras")
        return self

    def active_telescope(self) -> Telescope | None:
        """Return the active `Telescope`, or `None` if none is active.

        Returns
        -------
        telescope : `Telescope` or `None`
            The active telescope, or `None` if none is active.
        """
        if self.active_telescope_id is None:
            return None
        return next((t for t in self.telescopes if t.id == self.active_telescope_id), None)

    def active_camera(self) -> Camera | None:
        """Return the active `Camera`, or `None` if none is active.

        Returns
        -------
        camera : `Camera` or `None`
            The active camera, or `None` if none is active.
        """
        if self.active_camera_id is None:
            return None
        return next((c for c in self.cameras if c.id == self.active_camera_id), None)


class EquipmentConfiguration(BaseModel):
    """A resolved active telescope/camera pairing with derived geometry.

    Constructed from an `EquipmentCatalog`'s active entries rather than
    recorded independently, so there is exactly one place activeness is
    recorded (`Wayfinding_Library_Architecture.md` §2.2.2).
    """

    model_config = ConfigDict(populate_by_name=True)

    telescope: Telescope
    camera: Camera

    @property
    def plate_scale_arcsec_per_px(self) -> float:
        """Plate scale in arcseconds per pixel.

        Derived via: 206.265 x pixel_size_um / focal_length_mm.
        """
        return PLATE_SCALE_CONSTANT * self.camera.pixel_size_um / self.telescope.focal_length_mm

    @property
    def fov_width_deg(self) -> float:
        """Sensor field-of-view width in degrees."""
        return self.plate_scale_arcsec_per_px * self.camera.sensor_width_px / 3600.0

    @property
    def fov_height_deg(self) -> float:
        """Sensor field-of-view height in degrees."""
        return self.plate_scale_arcsec_per_px * self.camera.sensor_height_px / 3600.0
