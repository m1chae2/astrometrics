"""Purpose: Tests for the shared library input builder and the science goal.

Description: Checks the one definition each of the frame times, the target
direction, the set-aside reasons, the dominant setting and the science goal;
the reviewed night being left out of its own baseline; the telescope coming
from the capture setting; the observing site being read and never stored; and
that the copy of the unknown-setting camera name agrees with the efficiency
module (decision C2 of the code board).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration, CaptureSetting, FrameRecord, Target
from wayfindinglib.analytics.result_quality import efficiency, library_inputs
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation, baseline_key
from wayfindinglib.analytics.result_quality.library_inputs import (
    UNKNOWN_SETTING_CAMERA,
    LibraryInputs,
    dominant_setting,
    frame_times,
    set_aside_reasons,
    stored_site,
    target_direction,
    telescope_of,
)
from wayfindinglib.analytics.result_quality.science_goal import science_goal_for_target
from wayfindinglib.analytics.result_quality.sky_model import SkyModel
from wayfindinglib.analytics.test.night_quality_helpers import (
    FIRST_NIGHT,
    REVIEWED,
    SETTING,
    STAR,
    FakeLibrary,
    make_row,
    make_rows,
    make_target,
)
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.quality.efficiency import ScienceGoal

SITE = SiteProfile(id="default", name="Home", latitude_deg=45.76, longitude_deg=-110.74, elevation_m=1400.0)


@pytest.fixture
def config(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build an empty configuration in a temporary folder.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


def _butler(stored: SiteProfile | None = None) -> Any:
    """Build a stand-in butler that records any write.

    Returns
    -------
    butler : `SimpleNamespace`
        Reads the given site; ``writes`` lists every ``put``.
    """
    writes: list[Any] = []
    return SimpleNamespace(
        get=lambda kind, key=None: stored if kind == "site_profile" else None,
        get_all=lambda kind: [],
        put=lambda *arguments, **keywords: writes.append(arguments),
        writes=writes,
    )


def test_frame_times_are_mid_exposure_of_light_frames() -> None:
    """Each light frame's time is its start plus half its exposure."""
    target = make_target(STAR, 1, frames_per_night=2)
    target.frames.append(FrameRecord(path="/lib/darks/x.fits", role="DARK", timestamp=1.0, exposure="60"))

    times = frame_times(target)

    assert len(times) == 2
    assert min(times.values()) == pytest.approx(FIRST_NIGHT.timestamp() + 30.0)


def test_the_target_direction_leaves_out_unverified_and_placeholder_positions() -> None:
    """Only a verified, real position gives a direction."""
    target = make_target(STAR, 1)
    assert target_direction(target) is not None
    assert target_direction(target.model_copy(update={"position_unverified": True})) is None
    assert target_direction(Target(id="X", ra="0h 0m 0s", dec="0d 0m 0s")) is None
    assert target_direction(Target(id="Y")) is None


def test_only_a_row_that_says_excluded_marks_a_frame_set_aside() -> None:
    """Quarantine measurements alone do not set a frame aside."""
    target = make_target(STAR, 1, frames_per_night=2)
    moved, kept = target.frames
    rows = [
        make_row("frame.quarantine_roundness", 1, 0.9, target, moved, "frame moved to _excluded/"),
        make_row("frame.quarantine_roundness", 1, 0.9, target, kept),
    ]

    reasons = set_aside_reasons(rows)

    assert list(reasons.values()) == ["frame moved to _excluded/"]


def test_the_dominant_setting_holds_the_most_exposure() -> None:
    """The setting with the most exposure time wins."""
    other = SETTING.model_copy(update={"exposure_seconds": 120.0})
    observations = [
        FrameObservation(frame_id="a", capture_setting=SETTING, exposure_seconds=60.0),
        FrameObservation(frame_id="b", capture_setting=other, exposure_seconds=120.0),
    ]
    assert dominant_setting(observations) == other
    assert dominant_setting([]) is None


def test_the_unknown_setting_camera_copy_agrees_with_the_efficiency_module() -> None:
    """The copied constant must not drift from the private original."""
    assert UNKNOWN_SETTING_CAMERA == efficiency._UNKNOWN_SETTING_CAMERA


def test_the_science_goal_follows_the_kind_of_target() -> None:
    """Spectral frames, photometry, galaxies and stars get their own goals."""
    assert science_goal_for_target(Target(id="M 81")) == ScienceGoal.EXTENDED_OBJECT
    assert science_goal_for_target(Target(id="NGC 7331")) == ScienceGoal.EXTENDED_OBJECT
    assert science_goal_for_target(Target(id="HD 1")) == ScienceGoal.FAINT_POINT_SOURCE
    spectral = Target(id="Vega", frames=[FrameRecord(path="/a.fits", filter="Star Analyzer 200")])
    assert science_goal_for_target(spectral) == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION


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
    inputs = LibraryInputs(None, _butler(SITE), None)
    inputs._models[key] = (model, None)
    observation = FrameObservation(
        frame_id="f1",
        capture_setting=SETTING,
        start_epoch_seconds=datetime(2026, 10, 10, 6, 0, tzinfo=UTC).timestamp(),
        ra_deg=148.9,
        dec_deg=69.0,
    )
    unplaced = FrameObservation(frame_id="f2", capture_setting=SETTING)

    assert inputs.moonless_sky_rates([observation, unplaced]) == {"f1": pytest.approx(0.4)}


def test_the_site_is_read_never_stored_and_its_source_is_noted(config: AppConfiguration) -> None:
    """A missing stored site is rebuilt in memory; nothing is written."""
    butler = _butler(None)

    site = stored_site(butler, config)
    inputs = LibraryInputs(None, butler, config)
    assert inputs.site.id == site.id

    assert butler.writes == []
    assert any("No observing site is stored" in note for note in inputs.notes)
    assert LibraryInputs(None, _butler(SITE), config).site == SITE


def test_the_reviewed_night_is_left_out_of_its_own_baseline(config: AppConfiguration) -> None:
    """Three nights make a baseline; leaving one out leaves two, so none."""
    target = make_target(STAR, 3)
    library = FakeLibrary([target], make_rows(target, REVIEWED))
    inputs = LibraryInputs(library, _butler(SITE), config)
    inputs.targets()

    with_all = inputs.reference_choice(SETTING)
    without_one = inputs.reference_choice(SETTING, exclude_night_id=FIRST_NIGHT.date().isoformat())

    assert with_all.baseline is not None
    assert without_one.baseline is None
    assert "night" in (without_one.baseline_reason or "")


def test_the_telescope_comes_from_the_capture_setting(
    monkeypatch: pytest.MonkeyPatch, config: AppConfiguration
) -> None:
    """The optic named by the setting is used, not the active one."""
    small = SimpleNamespace(name="Small Scope", aperture_mm=80.0)
    large = SimpleNamespace(name="Large-Scope", aperture_mm=200.0)
    monkeypatch.setattr(library_inputs, "list_telescopes", lambda _config: [small, large])

    assert telescope_of(CaptureSetting(camera="C", telescope="large scope"), config) is large
    assert telescope_of(CaptureSetting(camera="C", telescope="Missing"), config) is None
    assert telescope_of(CaptureSetting(camera="C"), config) is None
