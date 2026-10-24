"""Purpose: Tests for sky samples, Moon-free rates and the science goal.

Description: Checks that sky rates are gathered with the time and place of
each frame, that frames without a time, a night, a setting or a target
position are left out, that a model gives each frame a rate without the Moon,
and that the science goal follows the target's kind (decision S6).
"""

from datetime import UTC, datetime

import pytest

from astrometricslib import CaptureSetting, FrameRecord, Target
from wayfindinglib.analytics.result_quality import sky_samples as ss
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation, baseline_key
from wayfindinglib.analytics.result_quality.science_goal import science_goal_for_target
from wayfindinglib.analytics.result_quality.sky_model import SkyModel
from wayfindinglib.analytics.test.night_quality_helpers import (
    FIRST_NIGHT,
    SETTING,
    STAR,
    make_rows,
    make_target,
)
from wayfindinglib.models.quality.efficiency import ScienceGoal


def test_frame_times_are_mid_exposure_of_light_frames() -> None:
    """Each light frame's time is its start plus half its exposure."""
    target = make_target(STAR, 1, frames_per_night=2)
    target.frames.append(FrameRecord(path="/lib/darks/x.fits", role="DARK", timestamp=1.0, exposure="60"))

    times = ss.frame_times(target)

    assert len(times) == 2
    assert min(times.values()) == pytest.approx(FIRST_NIGHT.timestamp() + 30.0)


def test_sky_samples_need_a_time_a_night_a_setting_and_a_position() -> None:
    """Frames missing any of these are left out."""
    target = make_target(STAR, 2)
    rows = [row for row in make_rows(target, "none") if row.metric == "frame.sky_rate_e_per_s_per_px"]
    unverified = target.model_copy(update={"position_unverified": True})

    full = ss.collect_sky_samples([target], rows, {1: SETTING})
    none_found = ss.collect_sky_samples([unverified], rows, {1: SETTING})
    no_setting = ss.collect_sky_samples([target], rows, {})

    assert sum(len(group) for group in full.values()) == 8
    assert none_found == {}
    assert no_setting == {}


def test_a_model_gives_a_rate_without_the_moon() -> None:
    """The no-Moon rate is the model constant when the airmass term is 0."""
    key = baseline_key(SETTING)
    model = SkyModel(
        setting_key=key,
        constant=0.4,
        airmass_coefficient=0.0,
        moon_coefficient=1.0,
        moon_fitted=True,
        nights_used=6,
        frames_used=60,
        in_sample_relative_error=0.05,
        held_out_relative_error=0.1,
    )
    observation = FrameObservation(
        frame_id="f1",
        capture_setting=SETTING,
        start_epoch_seconds=datetime(2026, 10, 10, 6, 0, tzinfo=UTC).timestamp(),
        ra_deg=148.9,
        dec_deg=69.0,
    )
    unplaced = FrameObservation(frame_id="f2", capture_setting=SETTING)
    other = FrameObservation(
        frame_id="f3",
        capture_setting=CaptureSetting(camera="Other"),
        start_epoch_seconds=1.0,
        ra_deg=1.0,
        dec_deg=1.0,
    )

    rates = ss.moonless_sky_rates([observation, unplaced, other], {key: model}, 45.76, -110.74, 1400.0)

    assert rates == {"f1": pytest.approx(0.4)}


def test_the_science_goal_follows_the_kind_of_target() -> None:
    """Spectral frames, photometry, galaxies and stars get their own goals."""
    assert science_goal_for_target(Target(id="M 81")) == ScienceGoal.EXTENDED_OBJECT
    assert science_goal_for_target(Target(id="NGC 7331")) == ScienceGoal.EXTENDED_OBJECT
    assert science_goal_for_target(Target(id="HD 1")) == ScienceGoal.FAINT_POINT_SOURCE
    spectral = Target(id="Vega", frames=[FrameRecord(path="/a.fits", filter="Star Analyzer 200")])
    assert science_goal_for_target(spectral) == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
