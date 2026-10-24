"""Purpose: Tests for the loss account and the night and target summaries.

Description: Checks that the account adds up, that set-aside frames stay
apart from the kept-frame deficit, that "cause unknown" exists only with an
independent noise check, that transparency is not included, and that the
summaries count frames, nights and settings.
"""

import pytest

from astrometricslib import CaptureSetting
from wayfindinglib.analytics.result_quality import aggregates as agg
from wayfindinglib.analytics.result_quality.efficiency import (
    BaselineNight,
    FrameObservation,
    baseline_key,
    baseline_reference,
)
from wayfindinglib.models.quality.efficiency import EfficiencyReference, ScienceGoal

SETTING = CaptureSetting(camera="CamA", telescope="Scope", filter_name="L", exposure_seconds=60.0)
OTHER = CaptureSetting(camera="CamA", telescope="Scope", filter_name="Ha", exposure_seconds=300.0)


def _reference_for(setting: CaptureSetting) -> EfficiencyReference | None:
    """Give a baseline reference for any setting.

    Parameters
    ----------
    setting : `CaptureSetting`
        The setting.

    Returns
    -------
    reference : `EfficiencyReference`
        A reference with width 2 and sky 0.5.
    """
    nights = [
        BaselineNight(
            night_id=str(i), median_star_width_effective_arcsec=2.0, median_sky_rate_e_per_s_per_px=0.5
        )
        for i in range(3)
    ]
    return baseline_reference(nights, baseline_key(setting)).reference


def _frame(
    frame_id: str,
    night: str = "n1",
    setting: CaptureSetting = SETTING,
    width: float = 3.0,
    sky: float = 1.0,
    **extra: object,
) -> FrameObservation:
    """Build one frame.

    Parameters
    ----------
    frame_id, night : `str`
        Frame and night ids.
    setting : `CaptureSetting`
        The capture setting.
    width, sky : `float`
        Star width and sky rate.
    **extra
        Other fields.

    Returns
    -------
    frame : `FrameObservation`
        The frame.
    """
    return FrameObservation(
        frame_id=frame_id,
        capture_setting=setting,
        night_id=night,
        target_id="T",
        exposure_seconds=setting.exposure_seconds,
        star_width_effective_arcsec=width,
        sky_rate_e_per_s_per_px=sky,
        read_noise_e=1.5,
        **extra,
    )


def _frames() -> list[FrameObservation]:
    """Build two nights of frames, one of them set aside.

    Returns
    -------
    frames : `list` [`FrameObservation`]
        Five frames.
    """
    return [
        _frame("a", "n1"),
        _frame("b", "n1", width=4.0),
        _frame("c", "n2", sky=2.0),
        _frame("d", "n2", set_aside_reason="clouded"),
        _frame("e", "n2", setting=OTHER),
    ]


def test_loss_account_adds_up_and_keeps_set_aside_apart() -> None:
    """Kept deficit equals captured minus effective.

    Set-aside time is a separate part.
    """
    result = agg.loss_account(_frames(), [], ScienceGoal.FAINT_POINT_SOURCE, _reference_for)
    assert result is not None
    account = result.account
    assert account.set_aside_seconds == pytest.approx(60.0)
    assert [share.cause for share in account.set_aside] == ["clouded"]
    summary = agg.target_summary("T", _frames(), ScienceGoal.FAINT_POINT_SOURCE, _reference_for)
    kept_captured = summary.captured_seconds - 60.0
    deficit = sum(share.seconds for share in account.kept_frame_deficit)
    assert deficit == pytest.approx(kept_captured - summary.efficiency_effective_seconds)
    assert account.transparency_included is False
    assert account.cause_unknown_seconds is None
    assert "Not measured" in account.cause_unknown_reason


def test_cause_unknown_exists_only_with_a_noise_check() -> None:
    """A stack noisier than its frames explain gets an unknown part.

    An agreeing one gets 0.
    """
    noisy = agg.MeasuredNoiseCheck(variance_ratio=1.5, variance_ratio_error=0.1)
    agreeing = agg.MeasuredNoiseCheck(variance_ratio=1.05, variance_ratio_error=0.1)
    with_noise = agg.loss_account(
        _frames(), [], ScienceGoal.FAINT_POINT_SOURCE, _reference_for, measured_noise_check=noisy
    )
    assert with_noise is not None
    assert with_noise.account.cause_unknown_seconds > 0.0
    with_match = agg.loss_account(
        _frames(), [], ScienceGoal.FAINT_POINT_SOURCE, _reference_for, measured_noise_check=agreeing
    )
    assert with_match.account.cause_unknown_seconds == pytest.approx(0.0)


def test_loss_account_passed_set_aside_frames_are_added() -> None:
    """Frames given as SetAsideInput count the same as marked frames."""
    extra = agg.SetAsideInput(frame_id="x", exposure_seconds=90.0, reason="trailed", capture_setting=SETTING)
    result = agg.loss_account([_frame("a")], [extra], ScienceGoal.FAINT_POINT_SOURCE, _reference_for)
    assert result.account.set_aside_seconds == pytest.approx(90.0)


def test_no_reference_gives_no_account_and_a_note() -> None:
    """When no setting has a reference there is no account.

    The summary says so.
    """
    assert agg.loss_account(_frames(), [], ScienceGoal.FAINT_POINT_SOURCE, lambda setting: None) is None
    summary = agg.target_summary("T", _frames(), ScienceGoal.FAINT_POINT_SOURCE, lambda setting: None)
    assert summary.loss_account is None
    assert summary.efficiency_effective_seconds is None
    assert any("No reference" in note for note in summary.notes)


def test_capture_setting_summaries_count_frames_nights_and_set_aside() -> None:
    """Each setting has its own counts.

    Captured time includes set-aside frames.
    """
    summaries = agg.capture_setting_summaries(
        _frames(),
        ScienceGoal.FAINT_POINT_SOURCE,
        _reference_for,
        weight_effective_seconds_by_setting={SETTING: 111.0},
    )
    by_filter = {s.capture_setting.filter_name: s for s in summaries}
    assert by_filter["L"].frames_taken == 4
    assert by_filter["L"].frames_set_aside == 1
    assert by_filter["L"].nights == 2
    assert by_filter["L"].captured_seconds == pytest.approx(240.0)
    assert by_filter["L"].weight_effective_seconds == pytest.approx(111.0)
    assert by_filter["Ha"].weight_effective_seconds is None
    assert by_filter["L"].efficiency_effective_seconds < 180.0
    assert by_filter["L"].loss_account is not None


def test_night_summary_uses_only_that_night() -> None:
    """A night's summary counts its own frames; a target summary counts all."""
    night = agg.night_summary("n1", _frames(), ScienceGoal.FAINT_POINT_SOURCE, _reference_for)
    assert night.scope == "night" and night.frames_taken == 2
    assert night.nights == ["n1"]
    target = agg.target_summary("T", _frames(), ScienceGoal.FAINT_POINT_SOURCE, _reference_for)
    assert target.frames_taken == 5 and target.nights == ["n1", "n2"]
    assert target.frames_set_aside == 1


def test_moon_share_appears_when_moonless_rates_are_given() -> None:
    """The account lists a model-derived Moon share of the sky loss."""
    moonless = {"a": 0.6, "b": 0.6, "c": 0.6}
    result = agg.loss_account(
        _frames(), [], ScienceGoal.FAINT_POINT_SOURCE, _reference_for, moonless_sky_rate_by_frame=moonless
    )
    shares = {share.cause: share for share in result.account.kept_frame_deficit}
    assert shares["sky_brightness_moon"].model_derived
