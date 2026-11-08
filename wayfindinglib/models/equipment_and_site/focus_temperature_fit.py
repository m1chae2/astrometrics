"""Purpose: The focus-versus-temperature fit, measured from the focus history.

Description: The result of fitting the focuser position that gave best focus
against the temperature at the time. Autofocus starts from this slope, and a
simulated focuser copies it. The model is a plain result and is never stored
(the stored one is `FocusModel`).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

FocusSource = Literal["autofocus_runs", "frames", "both"]
"""Where the focus history is read from: the autofocus runs Ekos recorded, the
focuser position and temperature in the light-frame headers, or both."""

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class FocusTemperatureFit(BaseModel):
    """How the best-focus position moves with temperature.

    Parameters
    ----------
    coefficient_steps_per_c : `float` or `None`
        The slope: focuser steps per degree Celsius. Negative means the
        best-focus position falls as the temperature rises. `None` when the
        history is too thin or too narrow for a fit; `message` says why.
    uncertainty_steps_per_c : `float` or `None`
        One standard error of the slope. `None` when the slope is.
    pairs_used : `int`
        The (temperature, best-focus position) pairs fitted.
    nights_used : `list` [`str`]
        The observing nights the pairs come from, sorted.
    nights_excluded : `list` [`str`]
        The nights left out on request.
    filters_used : `list` [`str`]
        The filters the pairs were taken through. Each filter has its own
        best-focus position; the fit gives them one slope and an offset each.
    temperature_range_c : `tuple` [`float`, `float`] or `None`
        The lowest and highest temperature among the pairs, in degrees
        Celsius. The slope is measured only over this range.
    residual_rms_steps : `float` or `None`
        What the pairs scatter around the fit, in focuser steps.
    source : `str`
        ``"autofocus_runs"`` (the positions Ekos chose), ``"frames"`` (the
        position and temperature written in the frame headers when the
        position changed) or ``"both"``.
    message : `str`
        One plain sentence about the fit.
    """

    model_config = _MODEL_CONFIG

    coefficient_steps_per_c: float | None = None
    uncertainty_steps_per_c: float | None = None
    pairs_used: int = 0
    nights_used: list[str] = Field(default_factory=list)
    nights_excluded: list[str] = Field(default_factory=list)
    filters_used: list[str] = Field(default_factory=list)
    temperature_range_c: tuple[float, float] | None = None
    residual_rms_steps: float | None = None
    source: str = ""
    message: str = ""
