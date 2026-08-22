"""Purpose: Unit tests for telemetry domain models.

Description: Verifies GuidingSample.total_drift and
AlignmentAttempt.pointing_error's Euclidean-norm calculations, carried
forward unchanged from `wayfindinglib.observatorylib.test.test_observatory`
per `Wayfinding_Library_Architecture.md` §2.4.7, plus basic
construction coverage for the previously untested telemetry models.
"""

import math

from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    GuidingSample,
    GuidingStats,
    GuidingStatus,
    IndiStatus,
)


def test_guiding_sample_total_drift() -> None:
    """Verify total_drift is the Euclidean norm of RA/Dec drift vectors."""
    sample = GuidingSample(time=10.5, dra=3.0, ddec=4.0, pulseRa=100.0, pulseDec=150.0)
    # The magnitude of vector (3, 4) is sqrt(3^2 + 4^2) = 5.0
    assert math.isclose(sample.total_drift, 5.0)


def test_alignment_attempt_pointing_error() -> None:
    """Verify pointing_error on AlignmentAttempt.

    Confirms it calculates the pointing error correctly when coordinate
    offsets are present, and returns None if they are absent.
    """
    # Test with valid pointing offsets
    attempt_with_offsets = AlignmentAttempt(status="aligned", deltaRaArcsec=6.0, deltaDecArcsec=8.0)
    # The magnitude of vector (6, 8) is sqrt(6^2 + 8^2) = 10.0
    assert math.isclose(attempt_with_offsets.pointing_error, 10.0)

    # Test with absent pointing offsets
    attempt_without_offsets = AlignmentAttempt(status="solving")
    assert attempt_without_offsets.pointing_error is None


def test_indi_status_defaults_to_unknown():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify IndiStatus defaults to UNKNOWN when unset."""
    assert IndiStatus().status == "UNKNOWN"


def test_guiding_status_defaults():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify GuidingStatus defaults to not guiding with zeroed stats."""
    status = GuidingStatus()
    assert status.is_guiding is False
    assert status.stats == GuidingStats()
    assert status.history == []
