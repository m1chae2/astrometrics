"""Purpose: Unit tests for ObservationSession and its recording.

Description: Verifies ObservationSession construction defaults, a real
save/load round-trip against wayfindinglib's own SQLite database (separate
from astrometricslib's), and find_quality_contributions_for_session's
lookup against a Target's quality-summary data.

Deliberately exercises `local_database.save_observation_session`/
`get_observation_session`/`load_observation_sessions` directly rather
than through `DiskButler`: since the three-function redesign,
`DiskButler`'s "observation_session" dataset type routes to
`wayfindinglib.models.session.observation_session.ObservationSession` (a
different, incompatible schema) under its own table --
`local_database`'s functions used here remain correct and unchanged,
serving this module's deprecated `ObservationSession` model until
`ObservationSessionRecorder` (the only other caller) is relocated onto
the new model.
"""

from astrometricslib import (
    AppConfiguration,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    Target,
    TargetSessionContribution,
)
from wayfindinglib.drivers import local_database
from wayfindinglib.observationlib.observation_session import ObservationSession
from wayfindinglib.observationlib.session_operations import find_quality_contributions_for_session


def test_observation_session_constructs_with_defaults():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the model can be constructed with just its required fields."""
    session = ObservationSession(id="2026-07-25", created_at="2026-07-25T22:00:00")
    assert session.target_session_id is None
    assert session.sequence_plan_id is None
    assert session.guiding_samples == []
    assert session.weather_samples == []


def test_observation_session_persistence_round_trip(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verifies save/load through wayfindinglib's own SQLite database."""
    config = AppConfiguration()
    had_section = config.app_config.has_section("Wayfinding Library")
    original_path = config.get_value("Wayfinding Library", "path", fallback=None)
    config.update_config({"Wayfinding Library": {"path": str(tmp_path)}})

    try:
        session = ObservationSession(
            id="M 13:2026-07-25", target_session_id="M 13:2026-07-25:100:10", created_at="2026-07-25T22:00:00"
        )
        local_database.save_observation_session(config, session)

        reloaded = local_database.get_observation_session(config, "M 13:2026-07-25")
        assert reloaded is not None
        assert reloaded.target_session_id == "M 13:2026-07-25:100:10"

        all_sessions = local_database.load_observation_sessions(config)
        assert len(all_sessions) == 1
    finally:
        if had_section:
            config.update_config({"Wayfinding Library": {"path": original_path}})
        else:
            config.app_config.remove_section("Wayfinding Library")
            config.save_configuration()


def test_find_quality_contributions_for_session_matches_target_session_breakdown(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the conduit finds a matching contribution across Targets."""
    target = Target(
        id="M 13",
        astrometry_quality_summary=AstrometryQualitySummary(
            target_id="M 13",
            target_session_ids=["M 13:2026-07-25:100:10"],
            target_session_breakdown=[
                TargetSessionContribution(
                    session_id="M 13:2026-07-25:100:10", frames_contributed=40, frames_clipped=2
                )
            ],
            astrometry_metrics=AstrometryPipelineQualityMetrics(
                sources_detected=50, solve_attempted=True, plate_solve_succeeded=True, simbad_matched_count=10
            ),
        ),
    )
    mocker.patch("astrometricslib.drivers.local_database.load_targets", return_value=[target])

    contributions = find_quality_contributions_for_session("M 13:2026-07-25:100:10")

    assert len(contributions) == 1
    assert contributions[0].target_id == "M 13"
    assert contributions[0].pipeline_name == "astrometry"
    assert contributions[0].contribution.frames_contributed == 40


def test_find_quality_contributions_for_session_returns_empty_when_no_match(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no contributions returned when no summary references session."""
    target = Target(id="M 13")
    mocker.patch("astrometricslib.drivers.local_database.load_targets", return_value=[target])

    assert find_quality_contributions_for_session("nonexistent-session") == []
