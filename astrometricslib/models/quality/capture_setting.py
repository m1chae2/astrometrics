"""The equipment and exposure settings a quality measurement was made with.

Many quality limits depend on how a frame was captured: a star width means
one thing at 400 mm focal length and another at 1000 mm. `CaptureSetting`
records those settings once, and each stored measurement points at it by id,
so the same settings are not repeated on every row.

Only the camera is required. A frame from an older session may lack some
header cards, and a partial record is still worth storing.

A caller also passes a `CaptureSetting` to say which calibration frames it
means (`calibration.query`, `assess` and `measure_sensor_noise`). The fields
it leaves empty are not compared.

The field names carry their units (``exposure_s``). The JSON names stay the
ones the app and the stored rows have always used (``gainSetting``,
``exposureSeconds``), and the old field names ``gain_setting`` and
``exposure_seconds`` are still read, so a record saved before the rename
loads unchanged.

`StoredCaptureSetting` pairs a stored setting with the id the quality store
gave it.
"""

import math

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

PIXEL_SCALE_SIGNIFICANT_FIGURES = TunedParameter(
    name="models.pixel_scale_significant_figures",
    default=4,
    unit="digits",
    description="Significant figures kept when rounding a pixel scale to identify a setup.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D29",
)
"""How many significant figures of the pixel scale a setting keeps.

The scale comes from the frame's FITS header, and capture programs write the
same number with different precision (``1.915279`` and ``1.9152790075`` for
the same camera and telescope, found on M 13 in the #134 copy restack). Kept
in full, the two would be two settings, and the frames of one setting would
be split between them. Four figures is 0.05% at a scale near 2 arcsec, far
finer than the 25% between this observatory's optics (300 and 405 mm), and
far coarser than the header rounding. Design estimate."""

UNKNOWN_CAPTURE_SETTING_CAMERA = "unknown capture setting"
"""The camera name for a frame whose rows point at no capture setting.

`CaptureSetting` needs a camera, so a reader that must describe a frame with no
setting row uses this text. It is a placeholder, not a real camera; a reader
that sees it knows the setting is unknown and must not treat it as equipment.
"""


def round_pixel_scale(value: float | None) -> float | None:
    """Round a pixel scale to a fixed number of significant figures.

    The model and the quality store's migration both use this one function,
    so a stored value and a model value always agree.

    Parameters
    ----------
    value : `float` or `None`
        The pixel scale as given, in arcseconds per pixel.

    Returns
    -------
    rounded : `float` or `None`
        The scale to `PIXEL_SCALE_SIGNIFICANT_FIGURES` significant figures;
        `None`, zero and values that are not finite are kept as given.
    """
    if value is None or value == 0 or not math.isfinite(value):
        return value
    digits = resolve(PIXEL_SCALE_SIGNIFICANT_FIGURES).value - 1 - math.floor(math.log10(abs(value)))
    return round(value, digits)


class CaptureSetting(BaseModel):
    """One combination of equipment and exposure settings.

    Parameters
    ----------
    camera : `str`
        The camera model name.
    telescope : `str` or `None`
        The telescope or lens name.
    focal_length_mm : `float` or `None`
        The focal length in millimeters.
    pixel_scale_arcsec : `float` or `None`
        The size of one pixel on the sky, in arcseconds per pixel, rounded to
        `PIXEL_SCALE_SIGNIFICANT_FIGURES` significant figures.
    filter_name : `str` or `None`
        The filter in the light path, such as ``"L"`` or ``"Ha"``.
    exposure_s : `float` or `None`
        The exposure time of one frame, in seconds.
    gain : `int` or `None`
        The camera's gain setting (a unitless number in the FITS ``GAIN``
        card, not electrons per ADU).
    offset : `int` or `None`
        The constant the camera adds to every pixel.
    binning : `int` or `None`
        The binning factor (1 means no binning).
    temperature_c : `float` or `None`
        The sensor temperature, in degrees Celsius. It says which darks a
        calibration call means. The quality store does not keep it, because
        a measurement's setting is the same at any temperature.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    camera: str
    telescope: str | None = None
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    filter_name: str | None = Field(default=None, alias="filterName")
    exposure_s: float | None = Field(
        default=None,
        alias="exposureSeconds",
        validation_alias=AliasChoices("exposureSeconds", "exposure_s", "exposure_seconds"),
    )
    gain: int | None = Field(
        default=None,
        alias="gainSetting",
        validation_alias=AliasChoices("gainSetting", "gain", "gain_setting"),
    )
    offset: int | None = None
    binning: int | None = None
    temperature_c: float | None = Field(
        default=None,
        alias="temperatureC",
        validation_alias=AliasChoices("temperatureC", "temperature_c"),
    )

    @classmethod
    def unknown(cls) -> CaptureSetting:
        """Build the stand-in for a frame whose rows point at no setting.

        Returns
        -------
        setting : `CaptureSetting`
            A setting whose camera is `UNKNOWN_CAPTURE_SETTING_CAMERA` and
            whose other fields are empty. `is_unknown` is `True` for it.
        """
        return cls(camera=UNKNOWN_CAPTURE_SETTING_CAMERA)

    @property
    def is_unknown(self) -> bool:
        """Whether this is the stand-in made by `unknown`, not real equipment.

        This is worked out every time and is not saved.
        """
        return self.camera == UNKNOWN_CAPTURE_SETTING_CAMERA

    @property
    def is_spectral(self) -> bool:
        """Whether the filter is the spectroscopy grating.

        The filter name is judged the way a frame's filter is (see
        `FrameRecord.is_spectral`). A setting with no filter name is not
        spectral. This is worked out every time and is not saved.
        """
        from astrometricslib.utilities.frame_identity import frame_is_spectral

        return frame_is_spectral({"filter": self.filter_name})

    @field_validator("pixel_scale_arcsec")
    @classmethod
    def _round_pixel_scale(cls, value: float | None) -> float | None:
        """Round the pixel scale, so header precision does not split a setting.

        Settings read back from the store are rounded too, so rows stored
        before the rounding group with the new ones.

        Parameters
        ----------
        value : `float` or `None`
            The pixel scale as given, in arcseconds per pixel.

        Returns
        -------
        rounded : `float` or `None`
            The value from `round_pixel_scale`.
        """
        return round_pixel_scale(value)


class StoredCaptureSetting(BaseModel):
    """A capture setting as the quality store keeps it, with its id.

    A stored measurement carries only a ``capture_setting_id``.
    ``astrometrics.quality.records.get_capture_settings`` gives these, so a
    reader can turn the id into the setting.

    Parameters
    ----------
    id : `int`
        The store's id of the setting.
    setting : `CaptureSetting`
        The camera, telescope, filter, exposure, gain, offset and binning.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    setting: CaptureSetting
