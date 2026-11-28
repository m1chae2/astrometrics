"""Purpose: `ReviewPerformance`, the limits of the equipment in use.

Description: The performance limits of the active equipment, the sky
background it should see at a pointing and time, and how its best focus
moves with temperature, all worked out on demand from the recorded nights
of this equipment. Nothing is stored. Each method is a thin wrapper of one
owner function.
"""

from __future__ import annotations

from astrometricslib.arguments import TimeInput
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.equipment_and_site.focus_temperature_fit import FocusSource, FocusTemperatureFit
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.sky_rate_prediction import SkyRatePrediction

__all__ = ["ReviewPerformance"]


class ReviewPerformance(ControlChild):
    """The equipment's limits, reached as ``wayfinder.review.performance``."""

    def get_envelope(
        self, *, until: TimeInput | None = None, exclude_nights: list[str] | None = None
    ) -> PerformanceEnvelope | None:
        """Work out the performance limits for the equipment in use now.

        Every limit is derived on the spot from the active equipment, the
        camera's stored profile, the star width this equipment's own frames
        show, and how it behaved in earlier sessions. Changing the active
        equipment changes every limit. A limit without enough data says so
        instead of giving a guess.

        Owner:
            wayfindinglib.tasks.control_tasks.performance_envelope_tasks.get_performance_envelope

        Parameters
        ----------
        until : `str`, `datetime` or `astropy.time.Time`, optional
            Use only the nights that began before the observing night of
            this moment for the baseline limits, such as
            ``"2026-09-24T22:00"`` for the nights before the night of
            2026-09-24. Every recorded night when omitted. Text without a
            time zone is UTC.
        exclude_nights : `list` [`str`], optional
            Nights to leave out of every source of the limits (guiding,
            session records, frames and plate solves), such as
            ``["2026-09-24"]``. Use it to fit the equipment without one
            night, then compare that night with the result.

        Returns
        -------
        envelope : `PerformanceEnvelope` or `None`
            The limits, with ``tracking_risk``: a grid scoring how risky
            each hour angle and declination is for tracking. `None` if no
            telescope and camera are active.

        Notes
        -----
        Raises `InvalidArgumentError` if `until` cannot be read as a time.
        """
        from wayfindinglib.tasks.control_tasks.performance_envelope_tasks import get_performance_envelope

        return get_performance_envelope(self._context, until=until, exclude_nights=exclude_nights)

    def predict_sky_rate(
        self,
        ra_deg: float,
        dec_deg: float,
        *,
        time: TimeInput | None = None,
        filter_name: str | None = None,
        exclude_nights: list[str] | None = None,
    ) -> SkyRatePrediction:
        """Predict the sky background rate for a pointing and a time.

        Uses the sky model fitted to the sky rates of the active telescope
        and camera's own frames: a dark-sky level, an airmass term and a
        Moon term. The prediction is labeled with its error on nights it was
        not fitted to.

        Owner:
            wayfindinglib.tasks.control_tasks.sky_rate_tasks.predict_sky_rate

        Parameters
        ----------
        ra_deg : `float`
            Right ascension the telescope points at, in ICRS degrees (as
            catalogs and plate solutions give it).
        dec_deg : `float`
            Declination the telescope points at, in ICRS degrees.
        time : `str`, `datetime` or `astropy.time.Time`, optional
            The moment. Now when omitted. Text without a time zone is UTC.
        filter_name : `str`, optional
            The filter. The setting with the most fitted nights when omitted.
        exclude_nights : `list` [`str`], optional
            Nights whose frames are left out of the model, for a
            leave-one-out check.

        Returns
        -------
        prediction : `SkyRatePrediction`
            The rate in electrons and in ADU per second per pixel, the
            Moon-free rate, and the model's error. When there is no model, or
            the pointing is below the horizon, the rates are `None` and
            ``message`` says why.

        Notes
        -----
        Raises `InvalidArgumentError` if `time` cannot be read or the
        declination is outside -90 to 90 degrees.
        """
        from wayfindinglib.tasks.control_tasks.sky_rate_tasks import predict_sky_rate

        return predict_sky_rate(
            self._context, ra_deg, dec_deg, time=time, filter_name=filter_name, exclude_nights=exclude_nights
        )

    def measure_focus_temperature(
        self, *, exclude_nights: list[str] | None = None, source: FocusSource = "both"
    ) -> FocusTemperatureFit:
        """Fit how the best focus moves with temperature.

        Uses the active telescope and camera's own history: the position
        Ekos chose in each recorded autofocus run, and the focuser position
        and temperature in the light-frame headers each time the position
        changed. Each filter gets its own offset and all share one slope.

        Owner:
            wayfindinglib.tasks.control_tasks.focus_model_tasks.measure_focus_temperature_coefficient

        Parameters
        ----------
        exclude_nights : `list` [`str`], optional
            Nights to leave out, for a leave-one-out check.
        source : `str`, optional
            ``"autofocus_runs"``, ``"frames"`` or ``"both"`` (the default: a
            night with autofocus runs uses them, and frame headers fill the
            other nights).

        Returns
        -------
        fit : `FocusTemperatureFit`
            The slope in focuser steps per degree Celsius with its
            uncertainty, the pairs and nights used, and the temperature range
            it covers. With too little history the slope is `None` and
            ``message`` says why.

        Notes
        -----
        Raises `InvalidArgumentError` for an unknown `source`.
        """
        from wayfindinglib.tasks.control_tasks.focus_model_tasks import measure_focus_temperature_coefficient

        return measure_focus_temperature_coefficient(self._context, exclude_nights, source)
