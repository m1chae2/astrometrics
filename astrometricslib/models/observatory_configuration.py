"""The equipment of the observatory, and the configurations it was used in.

An observatory configuration is one set of equipment used together: a
telescope, a main camera, and optionally a guide scope, a guide camera, a
focuser, a filter wheel and a mount, over a range of dates. Any change of
equipment makes a new configuration, so a frame taken on a given night
always points to the equipment it was taken with. The library keeps every
past configuration; the observatory control has one of them active.

`Camera`, `Telescope` and `GuideScope` are the one model of each piece of
equipment for both libraries. A camera's noise profile (`CameraProfile`) is
a calibration record about a camera, kept apart from this file.

This file only describes the shape of the data. The store is
`astrometricslib._internal.drivers.observatory_configuration_store`, and the
rules that seed configurations and match frames to them are in
`astrometricslib.pipelines.shared.equipment`.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astrometricslib.foundation.run_bases import PLATE_SCALE_CONSTANT

SIDEREAL_DEGREES_PER_HOUR = 15.0
"""Earth's sidereal rotation rate, in degrees of hour angle per hour."""

ARCSEC_PER_DEGREE = 3600.0
"""Arcseconds in one degree."""


class Telescope(BaseModel):
    """A telescope or camera lens, with the safe pointing limits of its mount.

    The focal length and focal ratio describe the optic. The altitude and
    hour angle limits, the meridian-flip point and the device protocols
    describe the mount that carries it and the devices on it.
    `flip_hour_angle_deg` is the one stored meridian-flip threshold;
    `meridian_flip_delay_min` is worked out from it
    (`Wayfinding_Library_Architecture.md` Section 5.5, Equation (3)).
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

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
    mount_protocol: str = Field(
        default="indi",
        description="Hardware-control protocol driving this telescope's mount.",
    )
    focuser_protocol: str = Field(
        default="indi",
        description=(
            "Hardware-control protocol driving this telescope's focuser. Lives here rather "
            "than on a standalone catalog entry because the focuser has no `EquipmentCatalog` "
            "entry of its own -- it is discovered heuristically off the same connection as "
            "the mount."
        ),
    )
    filter_wheel_protocol: str = Field(
        default="indi",
        description=(
            "Hardware-control protocol driving this telescope's filter wheel. Lives here "
            "rather than on a standalone catalog entry for the same reason as "
            "`focuser_protocol`."
        ),
    )
    guiding_protocol: str = Field(
        default="phd2",
        description=(
            "Which guider guides this telescope: 'phd2' (PHD2), 'internal' (KStars/Ekos "
            "sends the mount its own guide pulses) or 'simulator' (a stand-in guide loop)."
        ),
    )

    @model_validator(mode="after")
    def _check_altitude_envelope_ordering(self) -> Telescope:
        """Check that the altitude limits leave some sky to point at.

        Returns
        -------
        telescope : `Telescope`
            This telescope, unchanged, once checked.

        Raises
        ------
        ValueError
            If `min_altitude_deg` is above `max_altitude_deg`.
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

        Worked out from `flip_hour_angle_deg` at the sidereal rate
        (`Wayfinding_Library_Architecture.md` Section 5.5, Equation (3)):
        minutes = (degrees / 15) x 60.
        """
        return (self.flip_hour_angle_deg / SIDEREAL_DEGREES_PER_HOUR) * 60.0

    @property
    def aperture_mm(self) -> float | None:
        """Aperture diameter, in millimeters.

        Worked out from the focal length and the focal ratio: aperture =
        focal length / focal ratio. A focal ratio of 0 means "not set", so
        the aperture is then unknown.

        Returns
        -------
        aperture_mm : `float` or `None`
            The aperture, or `None` when the focal length or the focal ratio
            is missing or not above 0.
        """
        if not (self.focal_length_mm and self.focal_ratio) or self.focal_ratio < 0.0:
            return None
        return self.focal_length_mm / self.focal_ratio


class Camera(BaseModel):
    """A camera sensor's size and its cooling limits.

    `aliases` lists other spellings of the camera's name, such as the model
    names a capture program writes into the frame header.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    id: str
    name: str
    pixel_size_um: float = Field(..., gt=0.0, description="Pixel pitch in micrometres.")
    sensor_width_px: int = Field(..., gt=0, description="Sensor width in pixels.")
    sensor_height_px: int = Field(..., gt=0, description="Sensor height in pixels.")
    min_cooling_temp_c: float = Field(default=-30.0, description="Coldest temperature the sensor supports.")
    max_cooling_ramp_c_per_min: float = Field(
        default=2.0, gt=0.0, description="Maximum safe cooling/warming rate."
    )
    protocol: str = Field(
        default="indi",
        description="Hardware-control protocol driving this camera.",
    )
    aliases: tuple[str, ...] = Field(default=(), description="Other spellings of the camera's name.")


class GuideScope(BaseModel):
    """A separate small telescope used only for guiding.

    Its focal length sets the guide plate scale when guiding goes through it
    rather than through the main telescope (an off-axis guider uses the main
    telescope's focal length).
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    id: str
    name: str
    focal_length_mm: float = Field(..., gt=0.0, description="Effective focal length in millimetres.")
    aperture_mm: float | None = Field(default=None, gt=0.0, description="Clear aperture in millimetres.")


class Mount(BaseModel):
    """The mount that carries the telescope.

    Attributes
    ----------
    name : `str`
        The mount's name, as the settings list it.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)


class Focuser(BaseModel):
    """The focuser on the telescope.

    Attributes
    ----------
    name : `str`
        The focuser's name.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)


class FilterWheel(BaseModel):
    """The filter wheel and the filters and gratings in it.

    Attributes
    ----------
    name : `str`
        The filter wheel's name.
    filter_names : `tuple` [`str`, ...]
        The filters and gratings it holds.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    filter_names: tuple[str, ...] = ()


def configuration_key_for(camera_name: str, focal_length_mm: float) -> str:
    """Give the short key of a camera and a focal length.

    It is the same key that groups a target's frames into stacks, such as
    ``ZWO ASI533MM Pro@405mm``. The focal length is rounded to whole
    millimeters: no real optic differs from another by a fraction of one.

    Parameters
    ----------
    camera_name : `str`
        The camera's name.
    focal_length_mm : `float`
        The focal length, in millimeters.

    Returns
    -------
    key : `str`
        ``<camera>@<focal length>mm``.
    """
    return f"{camera_name.strip()}@{round(float(focal_length_mm))}mm"


class ObservatoryConfiguration(BaseModel):
    """One set of equipment used together over a range of dates.

    Any change of equipment is a new configuration; only the name, the
    notes and the dates of a stored one can change. A stored configuration
    has an `id` and a `revision`, the number of times it was saved.

    Attributes
    ----------
    id : `str`
        The stored configuration's id. Empty for one that is not stored yet.
    name : `str`
        A short name for people to read.
    telescope : `Telescope`
        The main telescope or lens.
    camera : `Camera`
        The main camera.
    guide_scope : `GuideScope` or `None`
        The guide scope, or `None` when guiding goes through the main
        telescope.
    guide_camera : `Camera` or `None`
        The guide camera, or `None` when the main camera guides.
    focuser : `Focuser` or `None`
        The focuser, when known.
    filter_wheel : `FilterWheel` or `None`
        The filter wheel, when known.
    mount : `Mount` or `None`
        The mount, when known.
    valid_from : `date` or `None`
        The first observing night the configuration was used, or `None`
        when not known.
    valid_until : `date` or `None`
        The last observing night, or `None` while it is still in use.
    notes : `str`
        Free text.
    revision : `int`
        How many times the stored configuration was saved; 0 before it is
        stored.
    """

    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str = ""
    telescope: Telescope
    camera: Camera
    guide_scope: GuideScope | None = None
    guide_camera: Camera | None = None
    focuser: Focuser | None = None
    filter_wheel: FilterWheel | None = None
    mount: Mount | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    notes: str = ""
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_date_order(self) -> ObservatoryConfiguration:
        """Check that the configuration does not end before it starts.

        Returns
        -------
        configuration : `ObservatoryConfiguration`
            This configuration, unchanged, once checked.

        Raises
        ------
        ValueError
            If `valid_until` is before `valid_from`.
        """
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError(f"valid_until ({self.valid_until}) is before valid_from ({self.valid_from})")
        return self

    def __repr__(self) -> str:
        """Give a short description of the configuration.

        Returns
        -------
        text : `str`
            The name, the key and the dates.
        """
        return (
            f"ObservatoryConfiguration({self.name!r}, {self.configuration_key!r}, "
            f"{self.valid_from} to {self.valid_until or 'now'})"
        )

    @property
    def configuration_key(self) -> str:
        """The camera and focal length key that groups frames into stacks.

        Returns
        -------
        key : `str`
            Such as ``ZWO ASI533MM Pro@405mm``.
        """
        return configuration_key_for(self.camera.name, self.telescope.focal_length_mm)

    def is_valid_on(self, night: date) -> bool:
        """Say whether the configuration was in use on an observing night.

        Parameters
        ----------
        night : `date`
            The observing night.

        Returns
        -------
        valid : `bool`
            `True` when the night is inside the date range. A missing start
            or end leaves that side open.
        """
        if self.valid_from is not None and night < self.valid_from:
            return False
        return self.valid_until is None or night <= self.valid_until

    @property
    def plate_scale_arcsec_per_px(self) -> float:
        """Plate scale of the main camera, in arcseconds per pixel.

        Worked out as 206.265 x pixel size (um) / focal length (mm).
        """
        return PLATE_SCALE_CONSTANT * self.camera.pixel_size_um / self.telescope.focal_length_mm

    def guider_plate_scale_arcsec_per_px(
        self, guide_scope: GuideScope | None = None, guide_camera: Camera | None = None
    ) -> float:
        """Plate scale to use for guider-calibration math.

        Uses the guide scope's focal length in place of the main
        telescope's, and the guide camera's pixel size in place of the main
        camera's, when those are given (or set on this configuration). Each
        falls back to the main telescope or camera (guiding through the main
        telescope with the main camera).

        Parameters
        ----------
        guide_scope : `GuideScope`, optional
            The guide scope. Defaults to this configuration's.
        guide_camera : `Camera`, optional
            The guide camera. Defaults to this configuration's.

        Returns
        -------
        plate_scale : `float`
            Arcseconds per pixel for guider-calibration math.
        """
        scope = guide_scope or self.guide_scope
        camera = guide_camera or self.guide_camera
        focal_length_mm = scope.focal_length_mm if scope else self.telescope.focal_length_mm
        pixel_size_um = camera.pixel_size_um if camera else self.camera.pixel_size_um
        return PLATE_SCALE_CONSTANT * pixel_size_um / focal_length_mm

    @property
    def fov_width_deg(self) -> float:
        """Field of view across the sensor's width, in degrees."""
        return self.plate_scale_arcsec_per_px * self.camera.sensor_width_px / ARCSEC_PER_DEGREE

    @property
    def fov_height_deg(self) -> float:
        """Field of view across the sensor's height, in degrees."""
        return self.plate_scale_arcsec_per_px * self.camera.sensor_height_px / ARCSEC_PER_DEGREE
