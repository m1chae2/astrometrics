"""Purpose: Unit tests for target quality advisory computation.

Description: Verifies build_target_quality_advisory aggregates flagged
status across pipelines and counts confirmed asteroid candidates,
against a lightweight fake target rather than a real Target instance.
"""

from dataclasses import dataclass, field
from typing import Any

from wayfindinglib.tasks.planning_tasks.quality_advisory_tasks import build_target_quality_advisory


@dataclass
class _FakeQualitySummary:
    """A stand-in quality summary: a flag and the reasons for it."""

    flagged: bool = False
    flag_reasons: list[str] = field(default_factory=list)


@dataclass
class _FakeStage:
    """A stand-in cascade stage holding its value."""

    value: str


class _FakeCandidate:
    """A stand-in asteroid candidate at one cascade stage."""

    def __init__(self, cascade_stage_value: str) -> None:
        """Record the stage the candidate reached."""
        self.cascade_stage = _FakeStage(cascade_stage_value)


@dataclass
class _FakeStacking:
    """A stand-in stacking result holding its quality summary."""

    quality_summary: _FakeQualitySummary | None = None


@dataclass
class _FakeQuality:
    """A stand-in set of analysis quality summaries."""

    astrometry: _FakeQualitySummary | None = None
    photometry: _FakeQualitySummary | None = None
    spectroscopy: _FakeQualitySummary | None = None


@dataclass
class _FakeAsteroidDetection:
    """A stand-in asteroid detection result."""

    quality_summary: _FakeQualitySummary | None = None
    candidates: list[_FakeCandidate] = field(default_factory=list)


class _FakeTarget:
    def __init__(self, **overrides: Any) -> None:
        self.id = "M 81"
        self.stacking = _FakeStacking(overrides.get("stack_quality_summary"))
        self.spectral_stacking = _FakeStacking(overrides.get("spectral_stack_quality_summary"))
        self.quality = _FakeQuality(
            astrometry=overrides.get("astrometry_quality_summary"),
            photometry=overrides.get("photometry_quality_summary"),
            spectroscopy=overrides.get("spectroscopy_quality_summary"),
        )
        self.asteroid_detection = _FakeAsteroidDetection(
            quality_summary=overrides.get("asteroid_detection_quality_summary"),
            candidates=overrides.get("asteroid_candidates", []),
        )


def test_advisory_aggregates_flagged_pipelines() -> None:
    """Verify flagged status/reasons aggregate across every pipeline."""
    target = _FakeTarget(
        stack_quality_summary=_FakeQualitySummary(flagged=True, flag_reasons=["low SNR"]),
        astrometry_quality_summary=_FakeQualitySummary(flagged=False),
    )
    advisory = build_target_quality_advisory(target)

    assert advisory.has_any_flagged() is True
    pipeline_names = {f.pipeline_name for f in advisory.quality_flags}
    assert pipeline_names == {"stacking", "astrometry"}


def test_advisory_counts_confirmed_asteroid_candidates() -> None:
    """Verify only ephemeris_matched candidates count as confirmed."""
    target = _FakeTarget(
        asteroid_candidates=[
            _FakeCandidate("morphology_detected"),
            _FakeCandidate("ephemeris_matched"),
            _FakeCandidate("ephemeris_matched"),
        ]
    )
    advisory = build_target_quality_advisory(target)

    assert advisory.science_outcomes.asteroid_candidate_count == 3
    assert advisory.science_outcomes.confirmed_asteroid_candidate_count == 2


def test_advisory_variable_star_count_always_zero() -> None:
    """Verify variable-star cross-reference is deferred, reporting zero."""
    target = _FakeTarget()
    advisory = build_target_quality_advisory(target)
    assert advisory.science_outcomes.variable_star_candidate_count == 0
