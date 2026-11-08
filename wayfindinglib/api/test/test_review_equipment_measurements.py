"""Purpose: Tests for the review measurements of the equipment itself.

Description: Checks `ObservationReview.measure_unguided_mount_error`,
`measure_guiding_spectrum`, `measure_focus_temperature_coefficient`,
`get_sky_rate_prediction` and the `exclude_nights` argument of
`get_performance_envelope`, on an isolated configuration with synthetic nights
recorded in the Butler and the log database. None of it touches a real library.
"""

from typing import ClassVar

import numpy as np
import pytest

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.reports import observing_night_id
from wayfindinglib import ObservationReview, ObservatoryControl
from wayfindinglib.analytics import capture_history
from wayfindinglib.analytics.capture_history import normalize_name
from wayfindinglib.analytics.test.unguided_error_helpers import RATE, synthetic_run
from wayfindinglib.analytics.unguided_mount_error import GuideRunSeries
from wayfindinglib.api.test.guiding_night_helpers import (
    FIRST_NIGHT,
    active_fingerprint,
    record_good_history,
    record_guiding_night,
)
from wayfindinglib.models.session.ekos_session import (
    EkosAutofocusRun,
    EkosSessionContext,
    SessionEquipmentAttribution,
)
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.tasks.control_tasks import focus_model_tasks, sky_rate_tasks


@pytest.fixture(autouse=True)
def _spell_cameras_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the camera-profile lookup the isolated configuration lacks."""
    monkeypatch.setattr(
        capture_history, "equipment_spellings", lambda astrometrics, name: {normalize_name(name)}
    )
    monkeypatch.setattr(
        sky_rate_tasks, "equipment_spellings", lambda astrometrics, name: {normalize_name(name)}
    )


def record_mount_night(control: ObservatoryControl, day: int, **run_options: float) -> str:
    """Record one synthetic guided night: samples and the guiding run.

    Returns
    -------
    night : `str`
        The observing night the samples were filed under.
    """
    start = FIRST_NIGHT + day * 86400.0
    night = observing_night_id(start)
    series: GuideRunSeries = synthetic_run(seed=day + 1, start=start, night=night, **run_options)
    control._context.records.replace_guiding_samples([
        {
            "timestamp": float(time),
            "dra": float(ra_error),
            "ddec": float(dec_error),
            "pulse_ra": float(ra_pulse),
            "pulse_dec": float(dec_pulse),
            "snr": 200.0,
            "source": "phd2_guide_log",
        }
        for time, ra_error, dec_error, ra_pulse, dec_pulse in zip(
            series.times,
            series.ra_error_arcsec,
            series.dec_error_arcsec,
            series.ra_pulse_ms,
            series.dec_pulse_ms,
            strict=True,
        )
    ])
    control.guiding._save_run(
        GuidingRunSummary(
            id=f"PHD2_GuideLog_{night}.txt#1",
            session_id=night,
            source_file_name=f"PHD2_GuideLog_{night}.txt",
            written_by_ekos=False,
            started_at=float(series.times[0]),
            ended_at=float(series.times[-1]) + 3.0,
            pixel_scale_arcsec_per_px=6.4,
            focal_length_mm=120.0,
            ra_rate_arcsec_per_second=RATE,
            dec_rate_arcsec_per_second=RATE,
            frames_total=int(series.times.size),
            samples_stored=int(series.times.size),
        )
    )
    return night


@pytest.fixture
def mount_nights(control: ObservatoryControl) -> list[str]:
    """Record three synthetic guided nights of the same mount.

    Returns
    -------
    nights : `list` [`str`]
        The night ids, oldest first.
    """
    return [record_mount_night(control, day) for day in range(3)]


def test_the_review_measures_the_mount_from_the_recorded_nights(
    control: ObservatoryControl, mount_nights: list[str]
) -> None:
    """The pooled error and drift match what the nights were built with."""
    result = ObservationReview(control).measure_unguided_mount_error()
    summary = result.summary
    assert [run.session_id for run in result.runs] == mount_nights
    assert summary.nights_used == mount_nights
    assert summary.periodic_error is not None
    assert summary.periodic_error.period_seconds == pytest.approx(480.0, rel=0.01)
    assert summary.periodic_error.amplitude_arcsec == pytest.approx(4.0, rel=0.1)
    assert summary.periodic_error.runs_used == 3
    assert summary.ra.drift_median_arcsec_per_minute == pytest.approx(0.5, abs=0.15)
    assert "arcsec" in result.message


def test_the_review_can_measure_one_night(control: ObservatoryControl, mount_nights: list[str]) -> None:
    """A night id limits the measurement to that night's runs."""
    result = ObservationReview(control).measure_unguided_mount_error(session_id=mount_nights[1])
    assert [run.session_id for run in result.runs] == [mount_nights[1]]


def test_excluded_nights_are_left_out_of_the_mount_measurement(
    control: ObservatoryControl, mount_nights: list[str]
) -> None:
    """A leave-one-out measurement uses the other nights and says which."""
    result = ObservationReview(control).measure_unguided_mount_error(exclude_nights=[mount_nights[2]])
    assert result.excluded_nights == [mount_nights[2]]
    assert result.summary.nights_used == mount_nights[:2]
    assert result.summary.periodic_error.runs_used == 2


def test_the_mount_measurement_stores_nothing(control: ObservatoryControl, mount_nights: list[str]) -> None:
    """Measuring leaves the saved guiding spectrum model untouched."""
    ObservationReview(control).measure_unguided_mount_error()
    assert control._context.active_guiding_spectrum_analysis() is None


def test_no_recorded_guiding_gives_an_empty_measurement(control: ObservatoryControl) -> None:
    """With nothing recorded there are no runs and no pooled numbers."""
    result = ObservationReview(control).measure_unguided_mount_error()
    assert result.runs == []
    assert result.summary.periodic_error is None


def test_the_guiding_spectrum_leaves_out_excluded_nights_and_stores_nothing(
    control: ObservatoryControl, mount_nights: list[str]
) -> None:
    """Leaving a night out removes its samples from the spectrum."""
    review = ObservationReview(control)
    everything = review.measure_guiding_spectrum()
    without_last = review.measure_guiding_spectrum(exclude_nights=[mount_nights[2]])
    one_night = review.measure_guiding_spectrum(session_id=mount_nights[0])
    assert without_last.sample_count == pytest.approx(everything.sample_count * 2 / 3, rel=0.01)
    assert one_night.sample_count == pytest.approx(everything.sample_count / 3, rel=0.01)
    assert control._context.active_guiding_spectrum_analysis() is None


def test_the_envelope_can_be_fitted_without_chosen_nights(control: ObservatoryControl) -> None:
    """Excluding a night removes it from the history behind the limits."""
    record_good_history(control)
    middle = record_guiding_night(control, 6, snr=30.0, lost=200)
    record_guiding_night(control, 7, snr=300.0)
    review = ObservationReview(control)

    everything = review.get_performance_envelope()
    without_middle = review.get_performance_envelope(exclude_nights=[middle])
    without_both = review.get_performance_envelope(
        exclude_nights=[middle, observing_night_id(FIRST_NIGHT + 7 * 86400.0)]
    )

    assert everything.thresholds["guide_snr_low_limit"].sample_count == 8
    assert without_middle.thresholds["guide_snr_low_limit"].sample_count == 7
    assert without_both.thresholds["guide_snr_low_limit"].sample_count == 6
    assert without_middle.value("guide_snr_low_limit") > everything.value("guide_snr_low_limit")


def test_excluding_the_latest_night_equals_judging_before_it(control: ObservatoryControl) -> None:
    """For the last night, leave-one-out and `before_night` agree."""
    record_good_history(control)
    last = record_guiding_night(control, 6, snr=30.0, lost=200)
    review = ObservationReview(control)
    assert review.get_performance_envelope(exclude_nights=[last]).value("guide_snr_low_limit") == (
        review.get_performance_envelope(before_night=last).value("guide_snr_low_limit")
    )


def _record_autofocus_night(control: ObservatoryControl, day: int, runs: list[tuple[float, int, str]]) -> str:
    """Record a session with autofocus runs of the active equipment.

    Parameters
    ----------
    control : `ObservatoryControl`
        The isolated control.
    day : `int`
        Days after the first synthetic night.
    runs : `list` [`tuple`]
        ``(temperature_c, final_position, filter_name)`` of each run.

    Returns
    -------
    night : `str`
        The observing night.
    """
    start = FIRST_NIGHT + day * 86400.0
    night = observing_night_id(start)
    control._context.butler.put(
        EkosSessionContext(
            id=f"session-{night}",
            session_id=night,
            started_at=start,
            ended_at=start + 7200.0,
            equipment=SessionEquipmentAttribution(
                equipment_fingerprint=active_fingerprint(),
                imaging_telescope_name="Short",
                imaging_camera_name="Main A",
            ),
            autofocus_runs=[
                EkosAutofocusRun(
                    timestamp=start + 600.0 * index,
                    succeeded=True,
                    temperature_c=temperature,
                    filter_name=filter_name,
                    final_position=position,
                )
                for index, (temperature, position, filter_name) in enumerate(runs)
            ],
        ),
        "ekos_session_context",
        {"id": f"session-{night}"},
    )
    return night


@pytest.fixture
def focus_history(control: ObservatoryControl) -> list[str]:
    """Record four nights of autofocus runs on a -12 steps per degree slope.

    Returns
    -------
    nights : `list` [`str`]
        The night ids, oldest first.
    """
    generator = np.random.default_rng(3)
    nights = []
    for day in range(4):
        runs = []
        for index in range(3):
            temperature = 12.0 - 2.0 * day - 1.5 * index
            position = 5000.0 - 12.0 * temperature + generator.normal(0.0, 3.0)
            runs.append((temperature, round(position), "L" if index % 2 == 0 else "R"))
        nights.append(_record_autofocus_night(control, day, runs))
    return nights


def test_the_focus_slope_is_fitted_from_autofocus_runs(
    control: ObservatoryControl, focus_history: list[str]
) -> None:
    """The slope, its uncertainty, the nights and the range are reported."""
    fit = ObservationReview(control).measure_focus_temperature_coefficient(source="autofocus_runs")
    assert fit.coefficient_steps_per_c == pytest.approx(
        -12.0, abs=max(4.0 * fit.uncertainty_steps_per_c, 0.5)
    )
    assert fit.uncertainty_steps_per_c > 0.0
    assert fit.nights_used == focus_history
    assert fit.pairs_used == 12
    assert fit.filters_used == ["L", "R"]
    assert fit.temperature_range_c[0] < fit.temperature_range_c[1]


def test_the_focus_fit_can_leave_a_night_out(control: ObservatoryControl, focus_history: list[str]) -> None:
    """An excluded night is not among the nights used."""
    fit = ObservationReview(control).measure_focus_temperature_coefficient(exclude_nights=[focus_history[0]])
    assert fit.nights_used == focus_history[1:]
    assert fit.nights_excluded == [focus_history[0]]


def test_the_focus_fit_says_why_when_the_history_is_thin(control: ObservatoryControl) -> None:
    """With no focus history there is no slope and a plain message."""
    fit = ObservationReview(control).measure_focus_temperature_coefficient()
    assert fit.coefficient_steps_per_c is None
    assert "needs" in fit.message


def test_the_focus_fit_refuses_an_unknown_source(control: ObservatoryControl) -> None:
    """A source other than the three names is refused."""
    with pytest.raises(InvalidArgumentError, match="source"):
        ObservationReview(control).measure_focus_temperature_coefficient(source="logs")  # type: ignore[arg-type]


def test_focus_pairs_from_frames_use_only_the_frames_where_the_position_changed() -> None:
    """Frames that repeat a position add no pair (it flattens the slope)."""

    class Frame:
        """A light-frame record with the focuser fields."""

        def __init__(self, time: float, position: int, temperature: float) -> None:
            """Fill the fields `pairs_from_frames` reads."""
            self.timestamp = FIRST_NIGHT + time
            self.focuser_position = position
            self.focuser_temperature_c = temperature
            self.filter = "L"

    frames = [
        (None, Frame(0, 5000, 10.0)),
        (None, Frame(600, 5000, 9.0)),
        (None, Frame(1200, 5000, 8.0)),
        (None, Frame(1800, 5012, 7.0)),
        (None, Frame(2400, 5012, 6.0)),
    ]
    pairs = focus_model_tasks.pairs_from_frames(frames)
    assert [(pair.temperature_c, pair.position) for pair in pairs] == [(10.0, 5000.0), (7.0, 5012.0)]


def test_the_thermal_fit_gives_one_slope_for_filters_with_different_offsets() -> None:
    """Filters have their own offsets and share one exact slope."""
    night = "2026-01-01"
    pairs = [
        focus_model_tasks.FocusPair(night, name, temperature, 5000.0 + offset - 10.0 * temperature)
        for name, offset in (("L", 0.0), ("R", 40.0))
        for temperature in (0.0, 2.0, 4.0, 6.0)
    ]
    fit = focus_model_tasks.fit_thermal_coefficient_with_uncertainty(pairs)
    assert fit.coefficient_steps_per_c == pytest.approx(-10.0)
    assert fit.residual_rms_steps == pytest.approx(0.0, abs=1e-6)


def test_the_thermal_fit_refuses_a_narrow_temperature_range() -> None:
    """A span under three degrees gives no slope."""
    pairs = [
        focus_model_tasks.FocusPair("2026-01-01", "L", 10.0 + 0.2 * index, 5000.0 - index)
        for index in range(8)
    ]
    fit = focus_model_tasks.fit_thermal_coefficient_with_uncertainty(pairs)
    assert fit.coefficient_steps_per_c is None
    assert "span" in fit.message


class _Model:
    """A stand-in sky model."""

    setting_key = "Main A|Short|L|gain100|offset10|bin1"
    frames_used = 100
    nights_used = 6


class _Inputs:
    """A stand-in for the library inputs that records the nights left out."""

    site = type("Site", (), {"latitude_deg": 45.0, "longitude_deg": -110.0, "elevation_m": 1500.0})()
    asked: ClassVar[list[tuple[str, ...]]] = []

    def __init__(self, *_arguments: object) -> None:
        """Ignore the library handles."""

    def sky_model_for(self, setting: object, exclude_nights: tuple[str, ...] = ()) -> tuple[object, None]:
        """Give the stand-in model and note which nights were excluded.

        Returns
        -------
        model, reason : `tuple`
            The model and no reason.
        """
        self.asked.append(tuple(exclude_nights))
        return _Model(), None


class _Setting:
    """A stand-in capture setting."""

    camera = "Main A"
    telescope = "Short"
    filter_name = "L"
    gain_setting = 100
    offset = 10
    binning = 1
    exposure_seconds = 60.0


def _stub_sky_library(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch, prediction: object
) -> None:
    """Replace the library handles the sky prediction reads with stand-ins."""
    quality = type("Quality", (), {"capture_settings": staticmethod(lambda: {"a": _Setting()})})()
    astrometrics = type("Astrometrics", (), {"quality": quality})()
    monkeypatch.setattr(control._context, "_astrometrics", astrometrics)
    monkeypatch.setattr(sky_rate_tasks, "LibraryInputs", _Inputs)
    monkeypatch.setattr(sky_rate_tasks, "baseline_key", lambda setting: _Model.setting_key)
    monkeypatch.setattr(sky_rate_tasks, "camera_gain_for_setting", lambda _astrometrics, _setting: 0.5)
    monkeypatch.setattr(
        sky_rate_tasks,
        "predict_sky_rate_at",
        lambda model, epoch, ra, dec, lat, lon, elevation: prediction,
    )


def test_the_sky_rate_prediction_reports_both_units_and_the_excluded_nights(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rate comes in electrons and ADU; exclusions reach the fit."""
    from wayfindinglib.analytics.result_quality.sky_model import SkyPrediction

    prediction = SkyPrediction(
        sky_rate_e_per_s_per_px=2.0,
        moonless_sky_rate_e_per_s_per_px=1.5,
        relative_error=0.2,
        moon_included=True,
        reliable=True,
    )
    _stub_sky_library(control, monkeypatch, prediction)
    _Inputs.asked.clear()
    result = ObservationReview(control).get_sky_rate_prediction(
        1.79e9, 150.0, 40.0, filter_name="L", exclude_nights=["2026-09-24"]
    )
    assert result.sky_rate_e_per_s_per_px == pytest.approx(2.0)
    assert result.sky_rate_adu_per_s_per_px == pytest.approx(4.0)
    assert result.electrons_per_adu == pytest.approx(0.5)
    assert result.nights_excluded == ["2026-09-24"]
    assert result.nights_used == 6
    assert _Inputs.asked == [("2026-09-24",)]


def test_the_sky_rate_prediction_refuses_an_impossible_declination(control: ObservatoryControl) -> None:
    """A declination beyond the pole is refused."""
    with pytest.raises(InvalidArgumentError, match="dec_deg"):
        ObservationReview(control).get_sky_rate_prediction(1.79e9, 150.0, 95.0)


def test_the_sky_rate_prediction_says_why_when_the_library_has_no_frames(control: ObservatoryControl) -> None:
    """With no frames of the equipment the answer is a message."""
    result = ObservationReview(control).get_sky_rate_prediction(1.79e9, 150.0, 40.0)
    assert result.sky_rate_e_per_s_per_px is None
    assert result.message
