"""Purpose: Unit tests for gathering a night's data for session analysis.

Description: Verifies how far the current limits are judged to apply to a
past night, how the guide-scale check is combined across sessions, and that
only measured samples are ever read.
"""

from pathlib import Path

import pytest

from astrometricslib import LoggerInterface
from wayfindinglib.models.session.ekos_session import EkosSessionContext, SessionEquipmentAttribution
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.tasks.control_tasks.session_analysis_tasks import (
    equipment_match_level,
    guide_scale_agreement,
    logged_guide_scale,
    measured_night_samples,
)

_ACTIVE = "telescope=apertura75q|camera=zwoasi533mmpro|guide_focal_mm=121|guide_scale=6.4"


def _run(focal: float | None = 121.05, scale: float | None = 6.39) -> GuidingRunSummary:
    """Build a guiding run with the given guide optics.

    Returns
    -------
    run : `GuidingRunSummary`
        A run with no frames.
    """
    return GuidingRunSummary(
        id="r#0",
        session_id="2026-09-23",
        source_file_name="r",
        written_by_ekos=True,
        started_at=1.0,
        focal_length_mm=focal,
        pixel_scale_arcsec_per_px=scale,
    )


def _context(guide_matches: bool | None) -> EkosSessionContext:
    """Build a session record with a given guide-scale verdict.

    Returns
    -------
    context : `EkosSessionContext`
        The session record.
    """
    return EkosSessionContext(
        id=f"c-{guide_matches}",
        session_id="2026-09-23",
        started_at=1.0,
        ended_at=2.0,
        equipment=SessionEquipmentAttribution(
            equipment_fingerprint="x", guide_scale_matches_configuration=guide_matches
        ),
    )


def test_a_night_on_the_current_setup_matches_exactly() -> None:
    """Verify a session with the active fingerprint gives an exact match."""
    assert equipment_match_level(_ACTIVE, ["other", _ACTIVE], [_run()]) == "exact"


def test_a_night_with_the_same_guide_optics_but_unknown_imaging_matches_partly() -> None:
    """Verify guide optics alone give a partial match."""
    unknown_imaging = "telescope=unknown|camera=unknown|guide_focal_mm=121|guide_scale=6.4"

    assert equipment_match_level(_ACTIVE, [unknown_imaging], [_run()]) == "guide_optics_only"


def test_a_night_with_different_guide_optics_does_not_match() -> None:
    """Verify different guide optics mean no limit applies."""
    assert equipment_match_level(_ACTIVE, ["x"], [_run(focal=240.0, scale=3.2)]) == "none"


def test_no_active_equipment_means_no_match() -> None:
    """Verify nothing matches when no equipment is active."""
    assert equipment_match_level(None, [_ACTIVE], [_run()]) == "none"


def test_a_night_with_no_guide_optics_recorded_does_not_match() -> None:
    """Verify missing optics cannot match, never guessed."""
    assert equipment_match_level(_ACTIVE, [], [_run(focal=None, scale=None)]) == "none"


def test_one_disagreeing_session_makes_the_night_disagree() -> None:
    """Verify a mismatch in any session wins."""
    assert guide_scale_agreement([_context(True), _context(False)]) is False


def test_agreeing_sessions_agree() -> None:
    """Verify agreement needs a verdict and no disagreement."""
    assert guide_scale_agreement([_context(True), _context(None)]) is True


def test_no_comparable_sessions_gives_no_verdict() -> None:
    """Verify an empty or unknown set gives `None`."""
    assert guide_scale_agreement([_context(None)]) is None
    assert guide_scale_agreement([]) is None


def test_the_logged_scale_is_the_most_common_one() -> None:
    """Verify the plate scale the night mostly used is reported."""
    assert logged_guide_scale([_run(scale=6.39), _run(scale=6.39), _run(scale=3.2)]) == pytest.approx(6.39)
    assert logged_guide_scale([]) is None


def test_only_measured_samples_are_read(tmp_path: Path) -> None:
    """Verify pulse-derived estimates and unverified samples are never read."""
    logger_interface = LoggerInterface(db_path=str(tmp_path / "log.db"))
    logger_interface.replace_guiding_samples([
        {"timestamp": 1790217108.0 + index, "source": "ekos_guide_log", "dra": 0.1, "ddec": 0.1}
        for index in range(3)
    ])
    logger_interface.replace_guiding_samples([
        {"timestamp": 1790217108.0 + index, "source": "indi_pulse_estimate", "dra": 9.0, "ddec": 9.0}
        for index in range(5)
    ])
    logger_interface.record_guiding_samples([{"timestamp": 1790217108.0, "dra": 9.0, "ddec": 9.0}])

    samples = measured_night_samples(logger_interface, "2026-09-23")

    assert len(samples) == 3
    assert {sample["source"] for sample in samples} == {"ekos_guide_log"}
