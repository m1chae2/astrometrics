"""Tests for the single chokepoint pipelines record their provenance through.

Covers the no-op behaviour when a run has no `job_id`, that a successful
run stamps `provenance_activity_id`/`upstream_entity_id` back onto its
summary, the helper functions runners use to note a stacked image as
their upstream input, and per-star `generated_by_job_id` stamping.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.drivers.provenance_store import ProvenanceStore
from astrometricslib.models.quality_summary import PipelineQualitySummaryBase
from astrometricslib.pipelines.shared.provenance_recording import (
    note_stacked_image_upstream,
    record_pipeline_run,
    stacked_image_entity_id,
    stamp_generated_by_job_id,
)
from astrometricslib.utilities import config_loader
from astrometricslib.utilities.config_loader import AppConfiguration


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Point the live configuration and logs database at a throwaway folder.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway job/provenance database.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(configuration, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: configuration)

    yield configuration.get_logs_db_path()


def make_summary(**overrides) -> PipelineQualitySummaryBase:  # ruff: ignore[missing-type-kwargs]
    """Build a minimal summary to record onto.

    Returns
    -------
    summary : `PipelineQualitySummaryBase`
        A summary with no provenance stamped yet.
    """
    defaults = {"pipeline_name": "photometry", "pipeline_version": "1.2.0", "target_id": "M13"}
    defaults.update(overrides)
    return PipelineQualitySummaryBase(**defaults)


def test_recording_with_no_job_id_does_nothing_at_all(isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check a job-less run (register_job=False) leaves the summary bare."""
    summary = make_summary()
    record_pipeline_run(
        summary=summary, job_id=None, target_id="M13", pipeline_name="photometry", pipeline_version="1.2.0"
    )
    assert summary.provenance_activity_id is None
    assert summary.upstream_entity_id is None


def test_a_successful_run_stamps_activity_and_upstream_ids_onto_the_summary(isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check the summary is stamped, and never raises on a storage failure."""
    summary = make_summary(resolved_parameters={"aperture_radius": 3.5, "use_annulus": True})
    record_pipeline_run(
        summary=summary,
        job_id="job-1",
        target_id="M13",
        pipeline_name="photometry",
        pipeline_version="1.2.0",
        upstream_entity_id="entity:stack-image:M13:abc",
    )
    assert summary.provenance_activity_id == "job-1"
    assert summary.upstream_entity_id == "entity:stack-image:M13:abc"


def test_a_successful_run_is_queryable_through_the_provenance_store(isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check `record_pipeline_run` wrote a real activity and lineage entry."""
    summary = make_summary()
    record_pipeline_run(
        summary=summary, job_id="job-1", target_id="M13", pipeline_name="photometry", pipeline_version="1.2.0"
    )
    store = ProvenanceStore(isolated_config)
    activity = store.get_activity("job-1")
    assert activity is not None
    assert activity.activity_description == "activitydesc:photometry:1.2.0"

    agent = store.get_agent_for_activity("job-1")
    assert agent is not None
    assert agent.id == "agent:photometry:1.2.0"

    lineage = store.get_lineage("M13")
    assert [entry.id for entry in lineage] == ["job-1"]


def test_a_pipeline_with_no_definition_still_records_without_raising(isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check an unrecognised pipeline name degrades instead of crashing."""
    summary = make_summary(pipeline_name="a_future_pipeline", pipeline_version="0.1.0")
    record_pipeline_run(
        summary=summary,
        job_id="job-1",
        target_id="M13",
        pipeline_name="a_future_pipeline",
        pipeline_version="0.1.0",
    )
    assert summary.provenance_activity_id == "job-1"


def test_stacked_image_entity_id_is_deterministic_and_target_scoped():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the same path always resolves to the same id, per target."""
    first = stacked_image_entity_id("M13", "/library/M13/stack.fits")
    second = stacked_image_entity_id("M13", "/library/M13/stack.fits")
    different_target = stacked_image_entity_id("M81", "/library/M13/stack.fits")
    assert first == second
    assert first != different_target


def test_note_stacked_image_upstream_sets_both_options_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the helper mutates the options dict a runner is building."""
    options: dict = {}
    note_stacked_image_upstream(options, "M13", "/library/M13/stack.fits", "input_image")
    expected_entity_id = stacked_image_entity_id("M13", "/library/M13/stack.fits")
    assert options["upstream_entity_id"] == expected_entity_id
    assert options["used_entity_ids"] == {expected_entity_id: "input_image"}


def test_note_stacked_image_upstream_does_nothing_with_no_stacked_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check a target with no stack yet leaves the options dict untouched."""
    options: dict = {}
    note_stacked_image_upstream(options, "M13", None, "input_image")
    assert options == {}


def test_stamp_generated_by_job_id_marks_the_matching_nested_result():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check photometry stamps `.photometry`; astrometry only real matches."""
    photometry_star = SimpleNamespace(photometry=SimpleNamespace(generated_by_job_id=None))
    stamp_generated_by_job_id("photometry", [photometry_star], "job-1")
    assert photometry_star.photometry.generated_by_job_id == "job-1"

    matched_star = SimpleNamespace(catalog_match_quality=SimpleNamespace(generated_by_job_id=None))
    unmatched_star = SimpleNamespace(catalog_match_quality=None)
    stamp_generated_by_job_id("astrometry", [matched_star, unmatched_star], "job-2")
    assert matched_star.catalog_match_quality.generated_by_job_id == "job-2"
    assert unmatched_star.catalog_match_quality is None


def test_stamp_generated_by_job_id_does_nothing_with_no_job_id():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check a run with no job leaves every star's provenance untouched."""
    star = SimpleNamespace(photometry=SimpleNamespace(generated_by_job_id="already-set"))
    stamp_generated_by_job_id("photometry", [star], None)
    assert star.photometry.generated_by_job_id == "already-set"


def test_stamp_generated_by_job_id_does_nothing_for_an_unrecognised_pipeline():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check asteroid detection, with no nested per-star result, is a no-op."""
    candidate = SimpleNamespace()
    stamp_generated_by_job_id("asteroid_detection", [candidate], "job-1")
    assert not hasattr(candidate, "generated_by_job_id")
