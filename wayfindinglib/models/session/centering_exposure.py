"""Purpose: The exposure chosen for the plate-solve centering frames.

Description: Centering (`control.mount.slew(destination, center=True)`) takes
short frames and plate solves them. A solve needs enough stars, and how many
stars a frame shows depends on the exposure. `CenteringExposure` is the answer
to "how long should the centering frame be?": the exposure, whether it was
worked out from the equipment's own measurements or taken from the fixed
setting, the reason, and every number the calculation used, so a person can
check it. The model is plain data and is never stored.
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class CenteringExposureTrial(BaseModel):
    """What one candidate exposure would show.

    Parameters
    ----------
    exposure_seconds : `float`
        The candidate exposure, in seconds.
    limiting_magnitude : `float`
        The faintest Gaia G magnitude a star can have and still be detected.
    expected_star_count : `int`
        Catalog stars in the field at or brighter than that magnitude.
    """

    model_config = _MODEL_CONFIG

    exposure_seconds: float
    limiting_magnitude: float
    expected_star_count: int


class CenteringExposure(BaseModel):
    """The centering exposure and why it was chosen.

    Parameters
    ----------
    exposure_seconds : `float`
        The exposure to use, in seconds.
    source : `str`
        ``"computed"`` when it was worked out from the equipment's
        measurements; ``"fallback"`` when an input was missing and the fixed
        ``alignment_exposure_seconds`` setting was used.
    reason : `str`
        One or two plain sentences saying why this exposure was chosen, or
        which input was missing.
    target_star_count : `int`
        How many detectable catalog stars the calculation asks for.
    limiting_magnitude : `float` or `None`
        Faintest detectable magnitude at the chosen exposure.
    expected_star_count : `int` or `None`
        Catalog stars in the field at or brighter than it.
    reaches_target : `bool`
        Whether the expected count reaches the target. `False` when even
        the longest allowed exposure falls short, or when the fallback ran.
    catalog_is_lower_bound : `bool`
        `True` when the star catalog is partly downloaded or shallower than
        the limiting magnitude, so the true count is at least the expected
        one and the chosen exposure is no shorter than needed.
    catalog_limit_magnitude : `float` or `None`
        Faintest magnitude of the installed star catalog.
    zero_point_mag : `float` or `None`
        Median Gaia G zero point of recent frames, for ADU per second.
    zero_point_frame_count : `int`
        Frames the zero point and the star width were taken from.
    star_width_arcsec : `float` or `None`
        Median effective star width of those frames, in arcseconds.
    sky_rate_e_per_s_per_px : `float` or `None`
        Predicted sky background, in electrons per second per pixel.
    read_noise_e : `float` or `None`
        Camera read noise from the camera profile, in electrons.
    gain_e_per_adu : `float` or `None`
        Camera gain from the camera profile, in electrons per ADU.
    pixel_scale_arcsec : `float` or `None`
        Arcseconds per pixel.
    field_radius_deg : `float` or `None`
        Radius of the circle in which stars were counted.
    setting_key : `str` or `None`
        The camera, telescope, filter, gain, offset and binning whose
        measurements were used.
    trials : `list` [`CenteringExposureTrial`]
        What each candidate exposure would show, shortest first.
    """

    model_config = _MODEL_CONFIG

    exposure_seconds: float
    source: str
    reason: str
    target_star_count: int
    limiting_magnitude: float | None = None
    expected_star_count: int | None = None
    reaches_target: bool = False
    catalog_is_lower_bound: bool = False
    catalog_limit_magnitude: float | None = None
    zero_point_mag: float | None = None
    zero_point_frame_count: int = 0
    star_width_arcsec: float | None = None
    sky_rate_e_per_s_per_px: float | None = None
    read_noise_e: float | None = None
    gain_e_per_adu: float | None = None
    pixel_scale_arcsec: float | None = None
    field_radius_deg: float | None = None
    setting_key: str | None = None
    trials: list[CenteringExposureTrial] = Field(default_factory=list)
