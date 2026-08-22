"""Purpose: Unit tests for target quality advisory computation.

Description: Verifies build_target_quality_advisory aggregates flagged
status across pipelines and counts confirmed asteroid candidates,
against a lightweight fake target rather than a real Target instance.
"""

import pytest

from wayfindinglib.tasks.planning_tasks.quality_advisory_tasks import build_target_quality_advisory


class _FakeQualitySummary:
    def __init__(self, flagged=False, flag_reasons=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.flagged = flagged
        self.flag_reasons = flag_reasons or []


class _FakeCandidate:
    def __init__(self, cascade_stage_value):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        class _Stage:
            value = cascade_stage_value

        self.cascade_stage = _Stage()


class _FakeTarget:
    def __init__(self, **overrides):  # ruff: ignore[missing-type-kwargs, missing-return-type-special-method]
        self.stack_quality_summary = overrides.get("stack_quality_summary")
        self.spectral_stack_quality_summary = overrides.get("spectral_stack_quality_summary")
        self.astrometry_quality_summary = overrides.get("astrometry_quality_summary")
        self.photometry_quality_summary = overrides.get("photometry_quality_summary")
        self.spectroscopy_quality_summary = overrides.get("spectroscopy_quality_summary")
        self.asteroid_recovery_quality_summary = overrides.get("asteroid_recovery_quality_summary")
        self.asteroid_candidates = overrides.get("asteroid_candidates", [])


class _FakeTargetRegistry:
    def __init__(self, targets):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._targets = targets

    def get(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self._targets.get(target_id)


class _FakeAstrometrics:
    def __init__(self, targets):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._targets = targets
        self.targets = _FakeTargetRegistry(targets)


def test_advisory_aggregates_flagged_pipelines():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify flagged status/reasons aggregate across every pipeline."""
    target = _FakeTarget(
        stack_quality_summary=_FakeQualitySummary(flagged=True, flag_reasons=["low SNR"]),
        astrometry_quality_summary=_FakeQualitySummary(flagged=False),
    )
    astrometrics = _FakeAstrometrics({"M 81": target})

    advisory = build_target_quality_advisory(astrometrics, "M 81")

    assert advisory.has_any_flagged() is True
    pipeline_names = {f.pipeline_name for f in advisory.quality_flags}
    assert pipeline_names == {"stacking", "astrometry"}


def test_advisory_counts_confirmed_asteroid_candidates():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify only ephemeris_matched candidates count as confirmed."""
    target = _FakeTarget(
        asteroid_candidates=[
            _FakeCandidate("morphology_detected"),
            _FakeCandidate("ephemeris_matched"),
            _FakeCandidate("ephemeris_matched"),
        ]
    )
    astrometrics = _FakeAstrometrics({"M 81": target})

    advisory = build_target_quality_advisory(astrometrics, "M 81")

    assert advisory.science_outcomes.asteroid_candidate_count == 3
    assert advisory.science_outcomes.confirmed_asteroid_candidate_count == 2


def test_advisory_variable_star_count_always_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify variable-star cross-reference is deferred, reporting zero."""
    target = _FakeTarget()
    astrometrics = _FakeAstrometrics({"M 81": target})
    advisory = build_target_quality_advisory(astrometrics, "M 81")
    assert advisory.science_outcomes.variable_star_candidate_count == 0


def test_advisory_raises_for_unknown_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a ValueError is raised when the target does not resolve."""
    astrometrics = _FakeAstrometrics({})
    with pytest.raises(ValueError):
        build_target_quality_advisory(astrometrics, "does-not-exist")
