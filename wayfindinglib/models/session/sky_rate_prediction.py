"""Purpose: A predicted sky background rate for one target, time and site.

Description: The answer to "how bright will the sky be for this camera, filter
and pointing at that moment?", from the sky model fitted to the library's own
frames. The rate is in electrons per second per pixel; divide by the camera's
gain (`electrons_per_adu`) for ADU per second. The model is plain data and is
never stored.
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class SkyRatePrediction(BaseModel):
    """A predicted sky rate with the model it came from.

    Parameters
    ----------
    sky_rate_e_per_s_per_px : `float` or `None`
        The predicted rate, in electrons per second per pixel. `None` when
        there is no model, or the target is below the horizon.
    moonless_sky_rate_e_per_s_per_px : `float` or `None`
        The same with the Moon's light taken out.
    sky_rate_adu_per_s_per_px : `float` or `None`
        The predicted rate in ADU per second per pixel
        (`sky_rate_e_per_s_per_px` over `electrons_per_adu`). `None` when
        either is.
    electrons_per_adu : `float` or `None`
        The camera's gain from its profile, in electrons per ADU.
    setting_key : `str` or `None`
        The equipment key of the model (camera, telescope, filter, gain,
        offset and binning).
    relative_error : `float` or `None`
        The model's error on a night it was not fitted to, as a fraction of
        the rate (0.2 is 20 percent).
    moon_included : `bool`
        Whether the model has a Moon term.
    reliable : `bool`
        False when the Moon is up but the model has no Moon term.
    nights_used : `int`
        Nights the model was fitted to.
    nights_excluded : `list` [`str`]
        The nights left out of the fit on request.
    message : `str`
        One plain sentence, including the reason when there is no rate.
    """

    model_config = _MODEL_CONFIG

    sky_rate_e_per_s_per_px: float | None = None
    moonless_sky_rate_e_per_s_per_px: float | None = None
    sky_rate_adu_per_s_per_px: float | None = None
    electrons_per_adu: float | None = None
    setting_key: str | None = None
    relative_error: float | None = None
    moon_included: bool = False
    reliable: bool = False
    nights_used: int = 0
    nights_excluded: list[str] = Field(default_factory=list)
    message: str = ""
