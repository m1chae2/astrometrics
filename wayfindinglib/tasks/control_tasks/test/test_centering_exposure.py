"""Purpose: Tests for choosing the centering exposure.

Description: Checks the signal-to-noise and limiting-magnitude formulas against
numbers worked out by hand, that the limit deepens with exposure, that the
shortest ladder step that reaches the star count is picked, that the ladder's
ends clamp the choice, that each missing input gives the fixed fallback with
its reason, and that a partly downloaded or shallow catalog is flagged. All
numbers are made up; no real library, catalog or camera is read.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.reports import CaptureSetting
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.sky_rate_prediction import SkyRatePrediction
from wayfindinglib.models.sky_position import SkyPosition
from wayfindinglib.tasks.control_tasks import centering_exposure
from wayfindinglib.tasks.control_tasks.centering_exposure import (
    EXPOSURE_LADDER_SECONDS,
    SensorAndSky,
    choose_centering_exposure,
    choose_exposure,
    limiting_magnitude,
    point_source_signal_to_noise,
    predict_star_counts,
    star_electrons,
)

FWHM_FOR_UNIT_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
"""A star width of one Gaussian sigma, so the noise area is exactly 4 pi."""


def _sensor(**changes: float) -> SensorAndSky:
    """Build the made-up sensor of the hand calculations.

    Returns
    -------
    sensor : `SensorAndSky`
        Zero point 20 (1 ADU per second at magnitude 20), gain 1, read
        noise 2 e, sky 1 e/s/px and a star width of one sigma.
    """
    values = {
        "zero_point_mag": 20.0,
        "gain_e_per_adu": 1.0,
        "read_noise_e": 2.0,
        "sky_rate_e_per_s_per_px": 1.0,
        "star_width_px": FWHM_FOR_UNIT_SIGMA,
    }
    values.update(changes)
    return SensorAndSky(**values)


def test_noise_area_of_a_one_sigma_star_is_four_pi() -> None:
    """The matched-filter noise area is 4 pi sigma squared."""
    assert _sensor().noise_pixels == pytest.approx(4.0 * math.pi)


def test_star_electrons_follow_the_zero_point_convention() -> None:
    """Magnitude 20 gives 1 ADU/s; 2.5 magnitudes brighter gives 10 times."""
    assert star_electrons(20.0, 10.0, _sensor(gain_e_per_adu=2.0)) == pytest.approx(20.0)
    assert star_electrons(17.5, 1.0, _sensor()) == pytest.approx(10.0)


def test_signal_to_noise_matches_a_hand_calculation() -> None:
    """S = 100 e, n = 4 pi, V = 14 give 100 / sqrt(100 + 175.93)."""
    ratio = point_source_signal_to_noise(17.5, 10.0, _sensor())
    signal = 10.0 * 10.0
    assert ratio == pytest.approx(signal / math.sqrt(signal + 4.0 * math.pi * 14.0))
    assert ratio == pytest.approx(6.0198, abs=1e-3)


def test_limiting_magnitude_matches_a_hand_calculation() -> None:
    """At 10 s the 5-sigma star has 79.987 e, which is magnitude 17.7425."""
    assert limiting_magnitude(10.0, _sensor()) == pytest.approx(17.7425, abs=1e-3)


def test_a_star_at_the_limit_has_the_detection_ratio() -> None:
    """The signal-to-noise ratio at the limiting magnitude is the threshold."""
    sensor = _sensor()
    for exposure in (1.0, 5.0, 30.0):
        magnitude = limiting_magnitude(exposure, sensor)
        assert point_source_signal_to_noise(magnitude, exposure, sensor) == pytest.approx(5.0)


def test_limit_deepens_with_exposure_and_with_less_sky() -> None:
    """A longer exposure and a darker sky both reach fainter stars."""
    sensor = _sensor()
    limits = [limiting_magnitude(seconds, sensor) for seconds in EXPOSURE_LADDER_SECONDS]
    assert limits == sorted(limits)
    assert len(set(limits)) == len(limits)
    assert limiting_magnitude(5.0, _sensor(sky_rate_e_per_s_per_px=0.1)) > limiting_magnitude(5.0, sensor)


def _field(count_by_magnitude: dict[float, int]) -> list[float]:
    """Make a star list with the given number of stars at each magnitude.

    Returns
    -------
    magnitudes : `list` [`float`]
        The magnitudes, repeated.
    """
    return [magnitude for magnitude, count in count_by_magnitude.items() for _ in range(count)]


def test_the_shortest_step_that_reaches_the_target_is_picked() -> None:
    """Twenty stars at 16.5 are reached by the first step deep enough."""
    sensor = _sensor()
    magnitudes = _field({16.5: 20})
    exposure, trials, reaches, _ = choose_exposure(sensor, magnitudes, target_star_count=15)
    deep_enough = [s for s in EXPOSURE_LADDER_SECONDS if limiting_magnitude(s, sensor) >= 16.5]
    assert exposure == deep_enough[0]
    assert reaches is True
    assert trials[-1].exposure_seconds == exposure
    assert trials[-1].expected_star_count == 20
    assert all(trial.expected_star_count == 0 for trial in trials[:-1])


def test_enough_stars_at_the_first_step_gives_the_shortest_exposure() -> None:
    """Many bright stars: the minimum, 1 s, is enough."""
    exposure, trials, reaches, _ = choose_exposure(_sensor(), _field({8.0: 40}))
    assert exposure == EXPOSURE_LADDER_SECONDS[0]
    assert len(trials) == 1
    assert reaches is True


def test_too_few_stars_clamps_at_the_longest_exposure() -> None:
    """Only 3 stars anywhere: the longest step is used, target missed."""
    exposure, trials, reaches, _ = choose_exposure(_sensor(), _field({10.0: 3}))
    assert exposure == EXPOSURE_LADDER_SECONDS[-1]
    assert len(trials) == len(EXPOSURE_LADDER_SECONDS)
    assert reaches is False


def test_the_count_never_falls_as_exposure_grows() -> None:
    """The expected count is non-decreasing over the ladder."""
    magnitudes = [10.0 + 0.1 * index for index in range(100)]
    _, trials, _, _ = choose_exposure(_sensor(), magnitudes, target_star_count=10_000)
    counts = [trial.expected_star_count for trial in trials]
    assert counts == sorted(counts)


def test_a_shallow_or_partial_catalog_is_a_lower_bound() -> None:
    """A catalog shallower than the limit, or not complete, flags the count."""
    magnitudes = _field({16.5: 20})
    assert choose_exposure(_sensor(), magnitudes, catalog_limit_magnitude=20.0)[3] is False
    assert choose_exposure(_sensor(), magnitudes, catalog_limit_magnitude=12.0)[3] is True
    assert choose_exposure(_sensor(), magnitudes, catalog_complete=False)[3] is True


# -- Gathering the inputs ---------------------------------------------------

NOW = datetime(2026, 11, 1, tzinfo=UTC).timestamp()


class _Quality:
    """Stands in for the library's quality measurements."""

    def __init__(
        self, zero_points: list[float | None], widths: list[float], zero_point_setting: int | None = 1
    ) -> None:
        """Keep the values to give out, all for capture setting 1.

        ``zero_point_setting=None`` gives the zero-point rows no capture
        setting, as the photometry pipeline stores them; the star width
        rows of the same frames still carry setting 1.
        """
        self._zero_point_setting = zero_point_setting
        self._rows = {
            centering_exposure.ZERO_POINT_METRIC: zero_points,
            centering_exposure.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC: widths,
        }

    def capture_settings(self) -> dict[int, CaptureSetting]:
        """Give the one capture setting.

        Returns
        -------
        settings : `dict` [`int`, `CaptureSetting`]
            Setting 1: the test camera and telescope, luminance, gain 0.
        """
        return {
            1: CaptureSetting(
                camera="Cam", telescope="Scope", filter_name="L", gain_setting=0, offset=10, binning=1
            )
        }

    def query(self, metric: str, **_: Any) -> SimpleNamespace:
        """Give the rows of a metric.

        Returns
        -------
        answer : `types.SimpleNamespace`
            With ``rows``.
        """
        rows = [
            SimpleNamespace(
                value=value,
                measured_at=datetime.fromtimestamp(NOW - index, UTC),
                subject=SimpleNamespace(
                    subject_id=f"T/Scope/Cam/frame_{index}.fits",
                    capture_setting_id=(
                        self._zero_point_setting if metric == centering_exposure.ZERO_POINT_METRIC else 1
                    ),
                ),
            )
            for index, value in enumerate(self._rows[metric])
        ]
        return SimpleNamespace(rows=rows)


def _profile(has_noise: bool = True, generic: bool = False) -> SimpleNamespace:
    """Build a camera profile stand-in.

    Returns
    -------
    profile : `types.SimpleNamespace`
        With ``is_generic_fallback`` and ``sensor_noise_for``.
    """
    entry = SimpleNamespace(
        gain_e_per_adu=SimpleNamespace(value=1.0), read_noise_e=SimpleNamespace(value=2.0)
    )
    return SimpleNamespace(
        is_generic_fallback=generic, sensor_noise_for=lambda *args, **kwargs: entry if has_noise else None
    )


def _context(quality: _Quality, profile: SimpleNamespace | None = None) -> SimpleNamespace:
    """Build the parts of a `ControlContext` the calculation reads.

    Returns
    -------
    context : `types.SimpleNamespace`
        With the settings, the configuration and the science library.
    """
    calibration = SimpleNamespace(camera_profile=lambda name: profile or _profile())
    return SimpleNamespace(
        config=object(),
        correction_config=CorrectionConfig(alignment_exposure_seconds=5.0),
        astrometrics=SimpleNamespace(quality=quality, processing=SimpleNamespace(calibration=calibration)),
    )


@pytest.fixture
def stand_ins(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the equipment, camera spellings and sky model with made-up ones.

    Returns
    -------
    knobs : `dict`
        ``sky`` holds the `SkyRatePrediction` to give out; change it to test
        a missing sky model.
    """
    knobs: dict[str, Any] = {"sky": SkyRatePrediction(sky_rate_e_per_s_per_px=1.0)}
    telescope = SimpleNamespace(name="Scope", focal_length_mm=405.0)
    camera = SimpleNamespace(name="Cam", pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008)
    catalog = SimpleNamespace(active_telescope=lambda: telescope, active_camera=lambda: camera)
    knobs["catalog"] = catalog
    monkeypatch.setattr(centering_exposure, "get_equipment_catalog", lambda config: knobs["catalog"])
    monkeypatch.setattr(centering_exposure, "equipment_spellings", lambda astrometrics, name: {"cam"})
    monkeypatch.setattr(
        centering_exposure.sky_rate_tasks, "get_sky_rate_prediction", lambda *args, **kwargs: knobs["sky"]
    )
    return knobs


def _choose(context: SimpleNamespace, stars: list[float] | None, status: dict | None = None) -> Any:
    """Run the choice with a made-up star catalog.

    Returns
    -------
    result : `CenteringExposure`
        The choice.
    """
    catalog_status = status or {"installed": True, "complete": True, "magnitude_limit": 20.7}
    return choose_centering_exposure(
        context,
        SkyPosition(ra_deg=10.0, dec_deg=20.0),
        epoch_seconds=NOW,
        find_stars=lambda *args: None if stars is None else [(i, 0.0, 0.0, m) for i, m in enumerate(stars)],
        catalog_status=lambda config: catalog_status,
    )


GOOD = _Quality([20.0] * 10, [3.6] * 10)


def test_the_inputs_give_a_computed_exposure(stand_ins: dict[str, Any]) -> None:
    """With every input, the result is computed and carries its numbers."""
    result = _choose(_context(GOOD), [16.5] * 30)
    assert result.source == "computed"
    assert result.exposure_seconds in EXPOSURE_LADDER_SECONDS
    assert result.reaches_target is True
    assert result.zero_point_mag == pytest.approx(20.0)
    assert result.pixel_scale_arcsec == pytest.approx(1.9144, abs=1e-3)
    assert result.field_radius_deg == pytest.approx(3008 * 1.9144 / 2 / 3600, abs=1e-3)
    assert result.star_width_arcsec == pytest.approx(3.6)
    assert result.trials[-1].exposure_seconds == result.exposure_seconds


def test_zero_points_without_a_setting_take_their_frames_setting(stand_ins: dict[str, Any]) -> None:
    """A zero point with no setting takes its frame's setting."""
    result = _choose(_context(_Quality([20.0] * 10, [3.6] * 10, zero_point_setting=None)), [16.5] * 30)
    assert result.source == "computed"
    assert result.zero_point_mag == pytest.approx(20.0)


def test_the_star_width_in_pixels_sets_the_noise_area(stand_ins: dict[str, Any]) -> None:
    """A wider star needs a longer exposure for the same field."""
    narrow = _choose(_context(_Quality([20.0] * 10, [2.0] * 10)), [16.5] * 30)
    wide = _choose(_context(_Quality([20.0] * 10, [8.0] * 10)), [16.5] * 30)
    assert wide.exposure_seconds >= narrow.exposure_seconds


@pytest.mark.parametrize(
    ("what", "expected"),
    [
        ("no zero point", "no recent Gaia G zero point"),
        ("few zero points", "fewer than"),
        ("no width", "no star width"),
        ("no equipment", "no imaging telescope"),
        ("generic camera", "no profile"),
        ("no noise entry", "no gain and read noise"),
        ("no sky", "no sky rate prediction"),
        ("no catalog", "not installed"),
        ("unreadable catalog", "could not be read"),
    ],
)
def test_each_missing_input_gives_the_fixed_setting_and_says_why(
    stand_ins: dict[str, Any], what: str, expected: str
) -> None:
    """A missing input gives the fixed 5 s setting and the reason."""
    quality, profile, stars, status = GOOD, None, [16.5] * 30, None
    if what == "no zero point":
        quality = _Quality([], [3.6])
    elif what == "few zero points":
        quality = _Quality([20.0] * 3, [3.6] * 3)
    elif what == "no width":
        quality = _Quality([20.0] * 10, [])
    elif what == "no equipment":
        stand_ins["catalog"] = SimpleNamespace(active_telescope=lambda: None, active_camera=lambda: None)
    elif what == "generic camera":
        profile = _profile(generic=True)
    elif what == "no noise entry":
        profile = _profile(has_noise=False)
    elif what == "no sky":
        stand_ins["sky"] = SkyRatePrediction(message="Below the horizon.")
    elif what == "no catalog":
        status = {"installed": False, "complete": False, "magnitude_limit": None}
    elif what == "unreadable catalog":
        stars = None

    result = _choose(_context(quality, profile), stars, status)

    assert result.source == "fallback"
    assert result.exposure_seconds == pytest.approx(5.0)
    assert expected in result.reason
    assert result.reaches_target is False


def test_a_changed_fallback_setting_is_used(stand_ins: dict[str, Any]) -> None:
    """The fallback follows `alignment_exposure_seconds`."""
    context = _context(_Quality([], []))
    context.correction_config = CorrectionConfig(alignment_exposure_seconds=8.0)
    assert _choose(context, [16.5] * 30).exposure_seconds == pytest.approx(8.0)


def test_a_partial_catalog_is_flagged_in_the_result(stand_ins: dict[str, Any]) -> None:
    """A catalog still downloading gives a lower bound, said so."""
    status = {"installed": True, "complete": False, "magnitude_limit": 20.7}
    result = _choose(_context(GOOD), [16.5] * 30, status)
    assert result.catalog_is_lower_bound is True
    assert "at least" in result.reason


def test_a_partial_catalog_with_no_stars_here_falls_back(stand_ins: dict[str, Any]) -> None:
    """A field the catalog has not reached gives the fallback."""
    status = {"installed": True, "complete": False, "magnitude_limit": 16.0}
    result = _choose(_context(GOOD), [], status)
    assert result.source == "fallback"
    assert "not been downloaded" in result.reason


def test_a_field_too_empty_for_the_target_uses_the_longest_exposure(stand_ins: dict[str, Any]) -> None:
    """Too few stars at any depth: 30 s is used and the reason warns."""
    result = _choose(_context(GOOD), [12.0] * 3)
    assert result.source == "computed"
    assert result.exposure_seconds == EXPOSURE_LADDER_SECONDS[-1]
    assert result.reaches_target is False
    assert "may fail" in result.reason


def test_the_metric_names_exist_in_the_library() -> None:
    """The two metric names read here are registered measurements."""
    from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry

    names = set(get_metric_registry().names())
    assert centering_exposure.ZERO_POINT_METRIC in names
    assert centering_exposure.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC in names


def test_predict_star_counts_gives_the_limit_and_count_per_exposure(
    stand_ins: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each asked exposure gets its own limit and a non-decreasing count."""
    from wayfindinglib.drivers.catalog import deep_star_store

    monkeypatch.setattr(
        deep_star_store,
        "find_deep_stars",
        lambda *args: [(i, 0.0, 0.0, 12.0 + 0.1 * i) for i in range(80)],
    )
    monkeypatch.setattr(
        deep_star_store, "read_catalog_status", lambda config: {"installed": True, "complete": True}
    )

    trials, facts, missing = predict_star_counts(
        _context(GOOD), SkyPosition(ra_deg=1.0, dec_deg=2.0), [2.0, 10.0], epoch_seconds=NOW
    )

    assert missing is None
    assert [trial.exposure_seconds for trial in trials] == [2.0, 10.0]
    assert trials[0].limiting_magnitude < trials[1].limiting_magnitude
    assert trials[0].expected_star_count <= trials[1].expected_star_count
    assert facts["field_radius_deg"] > 0.0


def test_predict_star_counts_names_a_missing_input(stand_ins: dict[str, Any]) -> None:
    """With no zero point the answer is empty and says why."""
    trials, facts, missing = predict_star_counts(
        _context(_Quality([], [])), SkyPosition(ra_deg=1.0, dec_deg=2.0), [5.0], epoch_seconds=NOW
    )
    assert trials == []
    assert facts == {}
    assert missing is not None
    assert "zero point" in missing
