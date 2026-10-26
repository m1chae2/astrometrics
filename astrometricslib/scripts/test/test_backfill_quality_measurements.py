"""Tests for the script that copies stored quality summaries into the table.

Target documents are built from the real summary models (dumped the way the
database stores them, with camelCase keys), so a renamed field breaks a test
instead of silently breaking the script. A temporary database holds them. The
tests check what the script must get right: nothing is written without
``--apply`` or without a backup, a second run writes nothing new, a stored row
equals the number in the JSON, old shapes are read, what cannot be copied is
counted, and the row-count check catches a difference.
"""

import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.gaia_xp_comparison import GaiaXpBandSummary, GaiaXpRunSummary
from astrometricslib.models.quality.gate_result import failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.quality.measurement import MeasurementOrigin, MeasurementStatus, SubjectLevel
from astrometricslib.models.quality.quality_summary import (
    AsteroidDetectionPipelineQualityMetrics,
    AsteroidDetectionQualitySummary,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    ExposureGroupSummary,
    PhotometryPipelineQualityMetrics,
    PhotometryQualitySummary,
    SpectroscopyPipelineQualityMetrics,
    SpectroscopyQualitySummary,
    StackingPipelineQualityMetrics,
    StackQualitySummary,
)
from astrometricslib.models.quality.spectroscopy_quality import StageQualityRollup
from astrometricslib.models.quality.stacking_quality import StackingInputQuality
from astrometricslib.models.wavelength_scale import WavelengthScaleSummary
from astrometricslib.pipelines.asteroid_detection import record_quality as asteroid_record
from astrometricslib.pipelines.astrometry.post_processing import record_quality as astrometry_record
from astrometricslib.pipelines.photometry.post_processing import record_quality as photometry_record
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.spectroscopy.post_processing import record_quality as spectroscopy_record
from astrometricslib.pipelines.stacking.post_processing import record_quality as stacking_record
from astrometricslib.scripts import backfill_quality_measurements as backfill

CREATED_AT = "2026-10-02T04:30:00+00:00"
STACK_PATH = "/mnt/stacks/M 31/M31_stack.fits"
STACK_NAME = "M31_stack.fits"
SPECTRAL_STACK_NAME = "M31_spectral.fits"
TARGET = "M 31"
# A frame time (seconds since the Unix epoch) used when a summary has no time.
FRAME_TIME = 1_759_000_000.0


def _dump(summary: Any) -> dict[str, Any]:
    """Dump a summary the way the database stores it.

    Returns
    -------
    document : `dict`
        The summary with camelCase keys and plain JSON values.
    """
    return summary.model_dump(by_alias=True, mode="json")


def stack_summary(*, with_groups: bool = True) -> dict[str, Any]:
    """Build a stacking summary with gates, input quality and groups.

    Returns
    -------
    summary : `dict`
        The stored summary.
    """
    groups = [
        ExposureGroupSummary(
            exposureSeconds=10.0,
            framesSubmitted=20,
            framesStacked=18,
            darkApplied=True,
            stackPath="/mnt/stacks/M 31/groups/M31_10s.fits",
            alignmentShiftPixels=[3.0, 4.0],
            gainMidRange=1.0,
            gainBrightEndRatio=0.97,
            gainDisagreement=0.03,
        ),
        ExposureGroupSummary(exposureSeconds=30.0, framesSubmitted=5, framesStacked=0, darkApplied=False),
    ]
    summary = StackQualitySummary(
        targetId=TARGET,
        createdAt=datetime.fromisoformat(CREATED_AT),
        stackingMetrics=StackingPipelineQualityMetrics(
            isSpectral=False,
            framesSubmitted=25,
            framesStacked=18,
            rejectedPixelFraction=0.04,
            flatFrameCount=30,
            flatNoiseFraction=0.002,
            flatSmoothingSigmaPx=1.5,
            saturatedPixelFraction=0.0001,
            zeroPixelFraction=0.01,
            negativePixelMaxPercent=12,
            stackedFwhmPx=3.1,
            medianInputFwhmPx=3.0,
            expectedStackFwhmPx=3.0,
            stackingDurationSeconds=61.5,
            registrationReferenceStarCount=210,
            exposureGroups=groups if with_groups else [],
        ),
        inputQuality=StackingInputQuality(
            framesSubmitted=25, framesAccepted=21, framesExcludedForGain=1, framesQuarantined=3
        ),
    )
    summary.record_gate(passed_gate("flat_noise", 0.002, 0.01, "the flat noise limit"))
    summary.record_gate(
        failed_gate("negative_pixels", "too many negative pixels", 12.0, 10.0, "STK-26 limit")
    )
    summary.record_gate(unchecked_gate("stack_sharpness", "no star width was measured"))
    summary.record_gate(passed_gate("zero_pixel_fraction", 0.01, 0.5, "validated"))
    return _dump(summary)


def astrometry_summary(version: str = "1.4.0") -> dict[str, Any]:
    """Build an astrometry summary with gates.

    Returns
    -------
    summary : `dict`
        The stored summary.
    """
    summary = AstrometryQualitySummary(
        targetId=TARGET,
        pipelineVersion=version,
        createdAt=datetime.fromisoformat("2026-10-03T01:00:00+00:00"),
        astrometryMetrics=AstrometryPipelineQualityMetrics(
            sourcesDetected=400,
            solveAttempted=True,
            plateSolveSucceeded=True,
            simbadMatchedCount=120,
            catalogMatchedStarCount=130,
            positionOnlyStarCount=200,
            unresolvedStarCount=70,
            catalogMatchSeparationRmsArcsec=1.2,
            plateSolveFitResidualRmsArcsec=0.8,
            plateSolveMatchedStarCount=90,
            gaiaFitRmsArcsec=0.5,
            gaiaFitRmsBeforeArcsec=0.9,
            gaiaFitStarCount=80,
            wcsRefinementShiftArcsec=0.3,
            plateScaleArcsecPerPixel=1.9,
            starFwhmPx=3.2,
            remoteCatalogQueriesAttempted=10,
            remoteCatalogQueriesFailed=2,
            plateSolveAttempts=1,
        ),
    )
    summary.record_gate(passed_gate("plate_solve", None, None, None, "solved"))
    summary.record_gate(passed_gate("astrometric_residual", 0.13, 0.5, "half a star width"))
    summary.record_gate(passed_gate("catalog_lookup", 0.2, 0.5, "lookups", "2 of 10 failed"))
    return _dump(summary)


def photometry_summary() -> dict[str, Any]:
    """Build a photometry summary with gates.

    Returns
    -------
    summary : `dict`
        The stored summary.
    """
    summary = PhotometryQualitySummary(
        targetId=TARGET,
        createdAt=datetime.fromisoformat("2026-10-03T02:00:00+00:00"),
        photometryMetrics=PhotometryPipelineQualityMetrics(
            starsProcessed=60,
            starsFound=64,
            framesProcessed=300,
            variableCandidateCount=2,
            crossSessionMatchCount=40,
            longTermVariableCandidateCount=1,
            astrometryIdentifiedStarCount=50,
            catalogMatchedStarCount=45,
            positionOnlyStarCount=10,
            unresolvedStarCount=5,
            lightCurveScatterRmsMag=0.012,
        ),
    )
    summary.record_gate(passed_gate("capture_timestamps", 0.0, 0.0))
    summary.record_gate(passed_gate("comparison_ensemble", 8.0, 5.0, "scatter test"))
    summary.record_gate(unchecked_gate("registration_drift", "no session was registered"))
    return _dump(summary)


def spectroscopy_summary(*, identification_counts: tuple[int, int, int] = (7, 3, 1)) -> dict[str, Any]:
    """Build a spectroscopy summary with roll-ups, Gaia XP bands and gates.

    Returns
    -------
    summary : `dict`
        The stored summary.
    """
    matched, position_only, unresolved = identification_counts
    summary = SpectroscopyQualitySummary(
        targetId=TARGET,
        createdAt=datetime.fromisoformat("2026-10-04T03:00:00+00:00"),
        spectroscopyMetrics=SpectroscopyPipelineQualityMetrics(
            zeroOrderSaturatedPixelFraction=0.02,
            dispersionAngleDeg=-1.5,
            medianTrailWidthPx=4.4,
            catalogMatchedStarCount=matched,
            positionOnlyStarCount=position_only,
            unresolvedStarCount=unresolved,
            poorMatchClassificationCount=1,
            ambiguousClassificationCount=2,
            stageQualitySummary=[
                StageQualityRollup(
                    stage="raw_frame",
                    spectrumCount=9,
                    metricMedians={"dar_along_dispersion_angstrom": 2.5},
                ),
                StageQualityRollup(stage="processing", spectrumCount=8),
            ],
            gaiaXpSummary=GaiaXpRunSummary(
                comparedStarCount=5,
                medianSlopePercentPer1000Angstrom=-3.0,
                medianAbsoluteSlopePercentPer1000Angstrom=4.0,
                bands=[
                    GaiaXpBandSummary(
                        startAngstrom=4200, endAngstrom=5000, medianRatio=1.1, scatter=0.05, starCount=5
                    ),
                    GaiaXpBandSummary(startAngstrom=5000, endAngstrom=6000, medianRatio=1.0, starCount=1),
                ],
            ),
            wavelengthScaleSummary=WavelengthScaleSummary(
                spectrumCount=6,
                appliedCount=4,
                rmsBeforeCorrectionAngstrom=3.0,
                rmsAfterCorrectionAngstrom=1.0,
                offsetSaturationCorrelation=0.4,
                correlationSpectrumCount=5,
            ),
        ),
    )
    summary.record_gate(passed_gate("spectra_extracted", 9.0, 1.0, "one spectrum"))
    summary.record_gate(unchecked_gate("wavelength_scale", "no offsets were measured"))
    return _dump(summary)


def asteroid_summary() -> dict[str, Any]:
    """Build an asteroid-detection summary with gates.

    Returns
    -------
    summary : `dict`
        The stored summary.
    """
    summary = AsteroidDetectionQualitySummary(
        targetId=TARGET,
        createdAt=datetime.fromisoformat("2026-10-05T05:00:00+00:00"),
        asteroidDetectionMetrics=AsteroidDetectionPipelineQualityMetrics(
            framesWithWcsEstimate=40,
            framesExcludedMissingPointingMetadata=0,
            candidatesDetected=9,
            candidatesPersistenceConfirmed=5,
            candidatesRateLinearityConfirmed=3,
            candidatesEphemerisMatched=2,
            ephemerisQueriesAttempted=6,
            ephemerisQueriesFailed=1,
            residualChainsTested=4,
            residualChainsRejected=1,
            residualChainsNonMonotonic=0,
            residualAssumedErrorDetections=0,
            residualWorstAcceptedRatio=1.3,
        ),
    )
    summary.record_gate(passed_gate("search_frames", 40.0, 3.0, "the setting"))
    summary.record_gate(failed_gate("ephemeris_cross_match", "the database was unreachable", 1.0, 0.0))
    summary.record_gate(failed_gate("track_residuals", "assumed errors were used", 3.0, 0.0))
    return _dump(summary)


def full_document() -> dict[str, Any]:
    """Build a target document that holds every kind of summary.

    Returns
    -------
    document : `dict`
        The target document in the current nested shape.
    """
    spectral = stack_summary(with_groups=False)
    return {
        "id": TARGET,
        "frames": [{"path": "a.fits", "timestamp": FRAME_TIME}],
        "stacking": {"stackedImage": STACK_PATH, "qualitySummary": stack_summary()},
        "spectralStacking": {
            "stackedImage": f"/mnt/stacks/M 31/{SPECTRAL_STACK_NAME}",
            "qualitySummary": spectral,
        },
        "asteroidDetection": {"candidates": [], "qualitySummary": asteroid_summary()},
        "quality": {
            "astrometry": astrometry_summary(),
            "photometry": photometry_summary(),
            "spectroscopy": spectroscopy_summary(),
        },
    }


def make_database(directory: Path, documents: dict[str, Any]) -> str:
    """Write a catalog database holding the given target documents.

    Parameters
    ----------
    directory : `pathlib.Path`
        Where the database file goes.
    documents : `dict`
        Target documents by id. A string value is stored as it is, so a test
        can store text that is not JSON.

    Returns
    -------
    database_path : `str`
        The database file.
    """
    path = directory / "astrometrics.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE targets (id TEXT PRIMARY KEY, name TEXT, ra TEXT, dec TEXT, data_json TEXT)"
    )
    for target_id, document in documents.items():
        text = document if isinstance(document, str) else json.dumps(document)
        connection.execute("INSERT INTO targets (id, data_json) VALUES (?, ?)", (target_id, text))
    connection.commit()
    connection.close()
    return str(path)


def quality_tables_exist(database_path: str) -> bool:
    """Say whether the database has the quality measurement table.

    Returns
    -------
    exists : `bool`
        True if the table exists.
    """
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'quality_measurements'").fetchone()
    finally:
        connection.close()
    return row is not None


def fake_backup(database_path: str) -> Any:
    """Make a backup function that records that it was called.

    Returns
    -------
    make_backup : `Callable`
        Returns a made-up path and counts its calls in ``calls``.
    """

    def make_backup() -> str:
        """Pretend to back up the database.

        Returns
        -------
        path : `str`
            A made-up backup path.
        """
        make_backup.calls += 1  # type: ignore[attr-defined]
        return f"{database_path}.bak"

    make_backup.calls = 0  # type: ignore[attr-defined]
    return make_backup


def all_rows(database_path: str) -> list[Any]:
    """Read every stored measurement.

    Returns
    -------
    rows : `list`
        All rows, oldest first.
    """
    return QualityStore(database_path).query(latest_only=False)


def row_for(rows: list[Any], metric: str, subject_id: str | None = None) -> Any:
    """Find the one row of a metric (and subject).

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    found = [
        row
        for row in rows
        if row.metric == metric and (subject_id is None or row.subject.subject_id == subject_id)
    ]
    assert len(found) == 1, f"{metric} {subject_id}: found {len(found)} rows"
    return found[0]


def test_every_registered_metric_of_a_pipeline_is_mapped_or_marked_as_not_held() -> None:
    """Every registered metric is mapped or marked as not held."""
    registered = set(get_metric_registry().names())
    for pipeline in ("stacking", "astrometry", "photometry", "spectroscopy", "asteroid_detection"):
        mapped: set[str] = set()
        for kind in backfill.SUMMARY_KINDS:
            if kind.pipeline_name == pipeline:
                mapped |= backfill.mapped_metric_names(kind)
        expected = {name for name in registered if name.startswith(f"{pipeline}.")}
        assert mapped == expected, f"{pipeline}: {sorted(mapped ^ expected)}"


def test_the_gate_table_matches_the_metric_and_tier_each_pipeline_uses() -> None:
    """A stored gate has the metric, tier and entry a new run gives it."""
    published = {
        "astrometry": astrometry_record.GATE_LIMIT_SOURCES,
        "photometry": photometry_record.GATE_LIMIT_SOURCES,
        "spectroscopy": spectroscopy_record.GATE_LIMIT_SOURCES,
        "asteroid_detection": asteroid_record.GATE_LIMIT_SOURCES,
    }
    for kind in backfill.SUMMARY_KINDS:
        if kind.pipeline_name == "stacking":
            table = stacking_record.GATE_LIMIT_SOURCES
        else:
            table = published[kind.pipeline_name]
        assert {m.gate_name for m in kind.gates} == set(table)
        for mapping in kind.gates:
            source = table[mapping.gate_name]
            assert source.metric_name == f"{kind.pipeline_name}.gate_{mapping.gate_name}"
            assert (mapping.tier, mapping.register_entry) == (source.tier, source.register_entry)


def test_the_group_subject_equals_the_one_the_stacking_record_step_uses() -> None:
    """A group's values are filed under the subject a new run would use."""
    context = PipelineRunContext(TARGET, "stacking", "1.3.0", "run-x", None, datetime.now(UTC))
    document = full_document()
    kind = backfill.SUMMARY_KINDS[0]
    planner = backfill.SummaryPlanner(
        kind, TARGET, document["stacking"]["qualitySummary"], datetime.now(UTC), "", "r", STACK_NAME
    )
    groups = document["stacking"]["qualitySummary"]["stackingMetrics"]["exposureGroups"]
    extra_group = {"exposureSeconds": 7.5, "stackPath": f"/x/{STACK_NAME}"}
    for index, raw in enumerate([*groups, extra_group]):
        model = ExposureGroupSummary.model_validate(
            raw | {"framesSubmitted": 1, "framesStacked": 1, "darkApplied": True}
        )
        assert backfill.exposure_group_subject_for(
            planner, raw, index
        ) == stacking_record.exposure_group_subject(context, STACK_NAME, model, index)


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    """Without --apply nothing is written and no backup is made."""
    database = make_database(tmp_path, {TARGET: full_document()})
    backup = fake_backup(database)
    report = backfill.run_backfill(database, make_backup=backup, log=lambda _: None)
    assert report.rows_planned["stacking"] > 0
    assert not report.applied
    assert backup.calls == 0
    assert not quality_tables_exist(database)


def test_applying_without_a_backup_function_is_refused(tmp_path: Path) -> None:
    """--apply needs a way to make the safety copy."""
    database = make_database(tmp_path, {TARGET: full_document()})
    with pytest.raises(ValueError, match="backup"):
        backfill.run_backfill(database, apply=True, log=lambda _: None)
    assert not quality_tables_exist(database)


def test_a_failed_backup_stops_before_any_write(tmp_path: Path) -> None:
    """A backup that could not be made leaves the table untouched."""
    database = make_database(tmp_path, {TARGET: full_document()})
    with pytest.raises(RuntimeError, match="backup"):
        backfill.run_backfill(database, apply=True, make_backup=lambda: None, log=lambda _: None)
    assert not quality_tables_exist(database)


def test_apply_writes_the_planned_rows_as_backfilled_and_a_second_apply_writes_none(tmp_path: Path) -> None:
    """The history is filled once; a repeat adds nothing."""
    database = make_database(tmp_path, {TARGET: full_document()})
    backup = fake_backup(database)
    report = backfill.run_backfill(database, apply=True, make_backup=backup, log=lambda _: None)
    planned = sum(report.rows_planned.values())
    assert planned > 100
    assert backup.calls == 1
    assert report.applied
    assert report.rows_written == planned
    assert report.mismatches == []
    assert not report.skipped.get(("stacking", backfill.SkipReason.REFUSED))
    assert all(reason is not backfill.SkipReason.REFUSED for _, reason in report.skipped)
    rows = all_rows(database)
    assert len(rows) == planned
    assert {row.origin for row in rows} == {MeasurementOrigin.BACKFILLED}
    assert {row.subject.level for row in rows} <= {SubjectLevel.RUN, SubjectLevel.STACK}
    assert all(row.run_id.startswith("backfill-M 31-") for row in rows)
    assert all("Backfilled from the stored" in row.reason for row in rows)

    second = backfill.run_backfill(database, apply=True, make_backup=backup, log=lambda _: None)
    assert second.rows_written == 0
    assert second.mismatches == []
    assert sum(second.rows_already_stored.values()) == planned
    assert len(all_rows(database)) == planned


def test_stored_values_equal_the_numbers_in_the_json(tmp_path: Path) -> None:
    """Every plain field that the JSON holds is stored with that number."""
    document = full_document()
    database = make_database(tmp_path, {TARGET: document})
    backfill.run_backfill(database, apply=True, make_backup=fake_backup(database), log=lambda _: None)
    rows = all_rows(database)
    checked = 0
    for kind in backfill.SUMMARY_KINDS:
        summary = backfill.find_summary(document, kind)
        assert summary is not None
        stack_name = backfill.file_name_of(backfill.first_present(document, kind.stack_image_paths))
        for mapping in kind.values:
            stored_value = backfill.first_present(summary, mapping.paths)
            if stored_value is None or backfill.as_number(stored_value) is None:
                continue
            subject_id = f"{TARGET}/{stack_name}" if mapping.subject is backfill.SubjectKind.STACK else None
            candidates = [row for row in rows if row.metric == mapping.metric]
            if subject_id is not None:
                candidates = [row for row in candidates if row.subject.subject_id == subject_id]
            assert len(candidates) == 1, mapping.metric
            assert candidates[0].value == pytest.approx(stored_value / mapping.divisor, rel=0, abs=0)
            checked += 1
    assert checked > 50


def test_rows_keep_the_pipeline_version_the_time_and_the_subjects_of_a_new_run(tmp_path: Path) -> None:
    """Version, time and subject match what the pipelines write."""
    database = make_database(tmp_path, {TARGET: full_document()})
    backfill.run_backfill(database, apply=True, make_backup=fake_backup(database), log=lambda _: None)
    rows = all_rows(database)

    rejected = row_for(rows, "stacking.rejected_pixel_fraction", f"{TARGET}/{STACK_NAME}")
    assert rejected.pipeline_version == "1.3.0"
    assert rejected.measured_at == datetime.fromisoformat(CREATED_AT)
    # The run id keeps the stored time text (pydantic writes "Z").
    assert rejected.run_id == f"backfill-{TARGET}-stacking-{CREATED_AT.replace('+00:00', 'Z')}"
    assert rejected.status is MeasurementStatus.MEASURED
    assert rejected.subject.level is SubjectLevel.STACK
    # Nobody checked this metric's estimator against the code's history, so
    # the definition version is the explicit "unknown" value.
    assert rejected.definition_version == 0
    assert "definition version is unknown" in rejected.reason

    negative = row_for(rows, "stacking.negative_pixel_max_fraction", f"{TARGET}/{STACK_NAME}")
    assert negative.value == pytest.approx(0.12)
    gate = row_for(rows, "stacking.gate_negative_pixels", f"{TARGET}/{STACK_NAME}")
    assert gate.value == pytest.approx(0.12)
    assert gate.check_limit is not None
    assert gate.check_limit.value == pytest.approx(0.10)
    assert gate.check_limit.register_entry == "STK-26"
    assert gate.check_outcome is not None
    assert gate.check_outcome.value == "failed"

    spectral = row_for(rows, "stacking.rejected_pixel_fraction", f"{TARGET}/{SPECTRAL_STACK_NAME}")
    assert spectral.value == pytest.approx(0.04)

    group = row_for(rows, "stacking.group_alignment_shift_px", f"{TARGET}/groups/M31_10s.fits")
    assert group.value == pytest.approx(5.0)
    unstacked = row_for(rows, "stacking.group_frames_stacked_count", f"{TARGET}/{STACK_NAME}#group-1-30s")
    assert unstacked.value == pytest.approx(0.0)

    solved = row_for(rows, "astrometry.gate_plate_solve")
    assert solved.value == pytest.approx(1.0)
    assert solved.subject.level is SubjectLevel.RUN
    assert solved.subject.subject_id.startswith(f"{TARGET}/astrometry/backfill-")
    attempts = row_for(rows, "astrometry.plate_solve_attempts_count")
    assert attempts.subject.level is SubjectLevel.RUN
    width = row_for(rows, "astrometry.star_width_gaussian_px")
    assert width.subject.subject_id == f"{TARGET}/{STACK_NAME}"
    lookup = row_for(rows, "astrometry.gate_catalog_lookup")
    assert lookup.value == pytest.approx(0.2)


def test_spectroscopy_tables_and_the_asteroid_gates_are_copied(tmp_path: Path) -> None:
    """Roll-ups, Gaia XP bands, wavelength scale and gates are stored."""
    database = make_database(tmp_path, {TARGET: full_document()})
    backfill.run_backfill(database, apply=True, make_backup=fake_backup(database), log=lambda _: None)
    rows = all_rows(database)

    assert row_for(rows, "spectroscopy.stage_raw_frame_spectrum_count").value == pytest.approx(9.0)
    assert row_for(rows, "spectroscopy.stage_raw_frame_dar_along_dispersion_angstrom").value == pytest.approx(
        2.5
    )
    ratio = row_for(rows, "spectroscopy.gaia_xp_band_4200_5000_median_ratio")
    assert ratio.value == pytest.approx(1.1)
    assert ratio.sample_count == 5
    assert row_for(rows, "spectroscopy.gaia_xp_band_5000_6000_star_count").value == pytest.approx(1.0)
    assert row_for(rows, "spectroscopy.wavelength_scale_rms_after_correction_angstrom").sample_count == 6
    assert row_for(rows, "spectroscopy.catalog_matched_star_count").value == pytest.approx(7.0)
    waiting = row_for(rows, "spectroscopy.gate_wavelength_scale")
    assert waiting.status is MeasurementStatus.NOT_MEASURED
    assert waiting.check_outcome is not None
    assert waiting.check_outcome.value == "not_checked"
    assert "no offsets were measured" in waiting.reason

    ephemeris = row_for(rows, "asteroid_detection.gate_ephemeris_cross_match")
    assert ephemeris.value == pytest.approx(1.0)
    assert ephemeris.check_limit is not None
    assert ephemeris.check_limit.value == pytest.approx(0.0)
    residuals = row_for(rows, "asteroid_detection.gate_track_residuals")
    assert residuals.status is MeasurementStatus.NOT_MEASURED
    assert row_for(rows, "asteroid_detection.residual_worst_accepted_ratio").value == pytest.approx(1.3)


def test_what_the_json_never_held_is_counted_not_written(tmp_path: Path) -> None:
    """Noise, weights and run-data gates are skipped and counted."""
    database = make_database(tmp_path, {TARGET: full_document()})
    report = backfill.run_backfill(database, log=lambda _: None)
    assert report.skipped["stacking", backfill.SkipReason.NOT_HELD] == 68
    assert report.skipped["astrometry", backfill.SkipReason.NOT_HELD] == 6
    assert report.skipped["photometry", backfill.SkipReason.NOT_HELD] == 10 + 9 + 1 + 6 + 3
    assert report.skipped["photometry", backfill.SkipReason.NEEDS_RUN_DATA] == 1
    rows = backfill_rows_planned(database)
    assert "stacking.frame_weight" not in rows
    assert "photometry.gate_comparison_ensemble" not in rows
    assert not any(name.startswith("frame.") for name in rows)


def backfill_rows_planned(database: str) -> set[str]:
    """Plan the rows of every target and list their metric names.

    Returns
    -------
    names : `set` [`str`]
        The planned metric names.
    """
    names: set[str] = set()
    for target_id, document in backfill.read_target_documents(database):
        assert document is not None
        for kind in backfill.SUMMARY_KINDS:
            summary = backfill.find_summary(document, kind)
            if summary is not None:
                plan = backfill.plan_summary(target_id, document, kind, summary)
                names.update(row.metric for row in plan.measurements)
    return names


def test_an_old_flat_summary_with_no_gates_is_read(tmp_path: Path) -> None:
    """Old top-level names, the old separation name and no gates work."""
    old_astrometry = astrometry_summary(version="1.2.0")
    old_astrometry["gates"] = []
    metrics = old_astrometry["astrometryMetrics"]
    metrics["astrometricResidualRmsArcsec"] = 2.5
    metrics["catalogMatchSeparationRmsArcsec"] = None
    old_stack = stack_summary()
    old_stack["inputQuality"] = None
    document = {
        "id": TARGET,
        "frames": [
            {"path": "a.fits", "timestamp": FRAME_TIME},
            {"path": "b.fits", "timestamp": FRAME_TIME + 60},
        ],
        "stackedImage": STACK_PATH,
        "stackQualitySummary": old_stack,
        "astrometryQualitySummary": old_astrometry,
    }
    database = make_database(tmp_path, {TARGET: document})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    assert report.mismatches == []
    assert report.summaries_found["stacking"] == 1
    assert report.summaries_found["astrometry"] == 1
    rows = all_rows(database)

    separation = row_for(rows, "astrometry.catalog_match_separation_rms_arcsec")
    assert separation.value == pytest.approx(2.5)
    assert separation.measured_at == datetime.fromisoformat("2026-10-03T01:00:00+00:00")
    assert separation.run_id.startswith(f"backfill-{TARGET}-astrometry-")
    names = {row.metric for row in rows}
    assert "astrometry.gaia_fit_rms_arcsec" not in names
    assert report.skipped["astrometry", backfill.SkipReason.ESTIMATOR_OLDER] == 4
    assert "stacking.frames_accepted_count" not in names
    assert not any(name.startswith("astrometry.gate_") for name in names)


def test_a_summary_with_no_pipeline_version_keeps_it_empty(tmp_path: Path) -> None:
    """An unknown version stays empty; changed estimators are skipped."""
    summary = astrometry_summary()
    del summary["pipelineVersion"]
    document = {"id": TARGET, "stacking": {"stackedImage": STACK_PATH}, "quality": {"astrometry": summary}}
    database = make_database(tmp_path, {TARGET: document})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    assert report.skipped["astrometry", backfill.SkipReason.ESTIMATOR_UNKNOWN] == 5
    rows = all_rows(database)
    assert {row.pipeline_version for row in rows} == {None}
    assert "unknown" in row_for(rows, "astrometry.sources_detected_count").reason


def test_a_summary_with_no_creation_time_is_skipped_even_when_frames_have_times(tmp_path: Path) -> None:
    """No createdAt gives no rows, whatever the frame times."""
    summary = photometry_summary()
    del summary["createdAt"]
    document = {
        "id": TARGET,
        "frames": [{"path": "a.fits", "timestamp": FRAME_TIME}],
        "quality": {"photometry": summary},
    }
    database = make_database(tmp_path, {TARGET: document})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    assert report.skipped["photometry", backfill.SkipReason.NO_TIME] == 1
    assert report.rows_written == 0
    assert not quality_tables_exist(database)


def test_stack_values_with_no_stacked_image_name_are_not_filed_under_the_run(tmp_path: Path) -> None:
    """A stack-level value is skipped when the target stores no stack name."""
    document = {"id": TARGET, "quality": {"astrometry": astrometry_summary()}}
    database = make_database(tmp_path, {TARGET: document})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    rows = all_rows(database)
    assert {row.subject.level for row in rows} == {SubjectLevel.RUN}
    assert report.skipped["astrometry", backfill.SkipReason.NO_STACK_FILE] > 10


def test_star_counts_that_are_all_zero_are_not_copied(tmp_path: Path) -> None:
    """Three zero counts look like an uncounted path, so they are skipped."""
    document = {
        "id": TARGET,
        "quality": {"spectroscopy": spectroscopy_summary(identification_counts=(0, 0, 0))},
    }
    database = make_database(tmp_path, {TARGET: document})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    assert report.skipped["spectroscopy", backfill.SkipReason.DEFAULT_ZERO] == 3
    names = {row.metric for row in all_rows(database)}
    assert "spectroscopy.catalog_matched_star_count" not in names
    assert "spectroscopy.poor_match_classification_count" in names


def test_the_per_frame_siril_columns_are_never_backfilled(tmp_path: Path) -> None:
    """The mislabeled registration columns stay out; no frame rows exist."""
    document = full_document()
    document["frames"] = [
        {
            "path": "a.fits",
            "timestamp": FRAME_TIME,
            "measurements": {"registrationRmse": 0.1, "registrationFwhmYPx": 3.0},
        }
    ]
    database = make_database(tmp_path, {TARGET: document})
    backfill.run_backfill(database, apply=True, make_backup=fake_backup(database), log=lambda _: None)
    assert all(row.subject.level is not SubjectLevel.FRAME for row in all_rows(database))


def test_the_row_count_check_catches_a_row_that_did_not_get_stored(tmp_path: Path) -> None:
    """Fewer stored rows than planned are reported as a mismatch."""

    class DroppingStore(QualityStore):
        """A store that silently loses the last row of every batch."""

        def write_measurements(self, measurements: Any) -> int:
            """Write all but the last measurement.

            Returns
            -------
            new_count : `int`
                How many rows were new.
            """
            return super().write_measurements(list(measurements)[:-1])

    database = make_database(tmp_path, {TARGET: full_document()})
    report = backfill.run_backfill(
        database,
        apply=True,
        make_backup=fake_backup(database),
        store=DroppingStore(database),
        log=lambda _: None,
    )
    assert len(report.mismatches) == len(backfill.SUMMARY_KINDS)
    assert "expected" in report.mismatches[0]
    assert "The row-count check FAILED" in backfill.format_report(report)


def test_the_row_count_check_catches_a_stored_value_that_differs(tmp_path: Path) -> None:
    """A stored number that differs from the JSON is a mismatch."""
    database = make_database(tmp_path, {TARGET: full_document()})
    backfill.run_backfill(database, apply=True, make_backup=fake_backup(database), log=lambda _: None)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE quality_measurements SET value = value + 1 WHERE metric = 'stacking.frames_stacked_count'"
    )
    connection.commit()
    connection.close()
    documents = backfill.read_target_documents(database)
    document = documents[0][1]
    assert document is not None
    kind = backfill.SUMMARY_KINDS[0]
    plan = backfill.plan_summary(TARGET, document, kind, backfill.find_summary(document, kind) or {})
    matched, mismatches = backfill.check_rows_against_plans(QualityStore(database), [plan])
    assert matched["stacking"] == len(plan.measurements) - 1
    assert len(mismatches) == 1
    assert "stacking.frames_stacked_count" in mismatches[0]


def test_target_and_limit_choose_which_targets_are_read(tmp_path: Path) -> None:
    """--target reads one target; --limit stops after that many."""
    documents = {name: full_document() | {"id": name} for name in ("A", "B", "C")}
    database = make_database(tmp_path, documents)
    assert backfill.run_backfill(database, target_id="B", log=lambda _: None).targets_read == 1
    assert backfill.run_backfill(database, limit=2, log=lambda _: None).targets_read == 2
    assert backfill.run_backfill(database, log=lambda _: None).targets_read == 3


def test_a_target_whose_json_cannot_be_read_is_counted(tmp_path: Path) -> None:
    """Bad JSON is counted and the other targets are still read."""
    database = make_database(tmp_path, {"A": "not json", TARGET: full_document()})
    report = backfill.run_backfill(database, log=lambda _: None)
    assert report.targets_read == 2
    assert report.skipped["target", backfill.SkipReason.UNREADABLE_TARGET] == 1
    assert report.summaries_found["stacking"] == 1


def test_values_that_are_not_finite_numbers_are_skipped(tmp_path: Path) -> None:
    """A text or boolean value where a number belongs is not stored."""
    summary = photometry_summary()
    summary["photometryMetrics"]["starsFound"] = "many"
    summary["photometryMetrics"]["framesProcessed"] = True
    database = make_database(tmp_path, {TARGET: {"id": TARGET, "quality": {"photometry": summary}}})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    assert report.skipped["photometry", backfill.SkipReason.BAD_VALUE] == 2
    names = {row.metric for row in all_rows(database)}
    assert "photometry.stars_found_count" not in names
    assert "photometry.stars_processed_count" in names


def test_the_estimator_table_compares_versions_by_number() -> None:
    """Version 1.10.0 is newer than 1.4.0, and unparsable text is unknown."""
    metric = "astrometry.gaia_fit_rms_arcsec"
    assert backfill.estimator_problem(metric, "1.10.0") is None
    assert backfill.estimator_problem(metric, "1.4.0") is None
    assert backfill.estimator_problem(metric, "1.3.9") is backfill.SkipReason.ESTIMATOR_OLDER
    assert backfill.estimator_problem(metric, "x") is backfill.SkipReason.ESTIMATOR_UNKNOWN
    assert backfill.estimator_problem(metric, None) is backfill.SkipReason.ESTIMATOR_UNKNOWN
    assert backfill.estimator_problem("stacking.frames_stacked_count", None) is None
    assert set(backfill.ESTIMATOR_CHANGED_AT) <= set(get_metric_registry().names())


def test_a_time_without_a_zone_is_read_as_utc_and_says_so() -> None:
    """A naive stored time becomes UTC and the note records the assumption."""
    moment, note = backfill.read_stored_time("2026-10-02T04:30:00") or (None, "")
    assert moment == datetime(2026, 10, 2, 4, 30, tzinfo=UTC)
    assert "UTC" in note
    assert backfill.read_stored_time("not a time") is None
    assert backfill.read_stored_time(None) is None
    assert math.isfinite(backfill.as_number(1) or math.nan)
    assert backfill.as_number(True) is None


def test_main_reports_a_missing_database_with_exit_code_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A database path that does not exist is an error, not a crash."""
    assert backfill.main(["--database", str(tmp_path / "missing.db")]) == 1
    assert "No catalog database" in capsys.readouterr().out


def test_main_dry_run_then_apply_on_a_copy_of_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line backs up beside the file, writes, then checks."""
    database = make_database(tmp_path, {TARGET: full_document()})
    assert backfill.main(["--database", database]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert not quality_tables_exist(database)
    assert not list(tmp_path.glob("*.bak"))

    assert backfill.main(["--database", database, "--apply"]) == 0
    output = capsys.readouterr().out
    assert "row-count check passed" in output
    assert len(list(tmp_path.glob("astrometrics.db.*.bak"))) == 1
    assert quality_tables_exist(database)


def test_a_metric_with_a_checked_estimator_history_keeps_its_registered_version(tmp_path: Path) -> None:
    """Only checked metrics keep their version; the rest are unknown."""
    database = make_database(tmp_path, {TARGET: full_document()})
    backfill.run_backfill(database, apply=True, make_backup=fake_backup(database), log=lambda _: None)
    rows = all_rows(database)
    registry = get_metric_registry()
    checked = row_for(rows, "astrometry.gaia_fit_rms_arcsec")
    assert checked.definition_version == registry.get("astrometry.gaia_fit_rms_arcsec").definition_version
    assert "estimator as registered" in checked.reason
    for row in rows:
        if row.metric in backfill.ESTIMATOR_CHANGED_AT:
            assert row.definition_version != 0
        else:
            assert row.definition_version == 0, row.metric


def test_the_registry_accepts_the_unknown_version_only_for_backfilled_rows() -> None:
    """A measured row with definition version 0 is refused."""
    from astrometricslib.foundation.errors import InvalidArgumentError
    from astrometricslib.pipelines.shared.quality.record_measurements import (
        check_measurement_against_registry,
    )

    plan = backfill.plan_summary(TARGET, full_document(), backfill.SUMMARY_KINDS[2], astrometry_summary())
    unknown = next(row for row in plan.measurements if row.definition_version == 0)
    check_measurement_against_registry(unknown)
    with pytest.raises(InvalidArgumentError, match="definition_version"):
        check_measurement_against_registry(unknown.model_copy(update={"origin": MeasurementOrigin.MEASURED}))


def _live_row_for(metric: str, run_id: str, measured_at: datetime, subject_id: str) -> Any:
    """Build a live (measured) astrometry row.

    Returns
    -------
    row : `QualityMeasurement`
        A measured row for the test target.
    """
    from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject

    spec = get_metric_registry().get(metric)
    return QualityMeasurement(
        metric=metric,
        definitionVersion=spec.definition_version,
        subject=QualitySubject(level=SubjectLevel.RUN, subjectId=subject_id, targetId=TARGET),
        status=MeasurementStatus.MEASURED,
        value=3.0,
        origin=MeasurementOrigin.MEASURED,
        pipelineVersion="1.4.0",
        runId=run_id,
        measuredAt=measured_at,
    )


def test_a_summary_is_skipped_when_its_own_live_run_already_stored_rows(tmp_path: Path) -> None:
    """The run that made the latest summary is not stored a second time."""
    summary = astrometry_summary()
    summary["provenanceActivityId"] = "job-77"
    document = {"id": TARGET, "stacking": {"stackedImage": STACK_PATH}, "quality": {"astrometry": summary}}
    database = make_database(tmp_path, {TARGET: document})
    # A live run measures before its summary is created, so the time
    # rule alone would miss it.
    early = datetime.fromisoformat("2026-10-03T00:59:00+00:00")
    live = _live_row_for(
        "astrometry.plate_solve_attempts_count", "job-77", early, f"{TARGET}/astrometry/job-77"
    )
    QualityStore(database).write_measurements([live])
    report = backfill.run_backfill(database, log=lambda _: None)
    assert report.rows_planned["astrometry"] == 0
    assert report.skipped["astrometry", backfill.SkipReason.LIVE_ROWS_EXIST] == 1


def test_a_summary_is_skipped_when_a_live_row_is_at_or_after_its_time(tmp_path: Path) -> None:
    """A live row measured after the summary blocks the fill."""
    document = {
        "id": TARGET,
        "stacking": {"stackedImage": STACK_PATH},
        "quality": {"astrometry": astrometry_summary()},
    }
    database = make_database(tmp_path, {TARGET: document})
    later = datetime.fromisoformat("2026-10-03T01:00:00+00:00")
    QualityStore(database).write_measurements([
        _live_row_for(
            "astrometry.plate_solve_attempts_count", "run-abc", later, f"{TARGET}/astrometry/run-abc"
        )
    ])
    report = backfill.run_backfill(database, log=lambda _: None)
    assert report.skipped["astrometry", backfill.SkipReason.LIVE_ROWS_EXIST] == 1


def test_older_live_rows_and_other_pipelines_do_not_block_the_fill(tmp_path: Path) -> None:
    """Only a recent live row of the same pipeline blocks the fill."""
    document = {
        "id": TARGET,
        "stacking": {"stackedImage": STACK_PATH},
        "quality": {"astrometry": astrometry_summary()},
    }
    database = make_database(tmp_path, {TARGET: document})
    older = datetime.fromisoformat("2026-09-01T00:00:00+00:00")
    newer = datetime.fromisoformat("2026-10-09T00:00:00+00:00")
    QualityStore(database).write_measurements([
        _live_row_for(
            "astrometry.plate_solve_attempts_count", "run-old", older, f"{TARGET}/astrometry/run-old"
        ),
        _live_row_for(
            "asteroid_detection.candidates_detected_count",
            "run-other",
            newer,
            f"{TARGET}/asteroid_detection/run-other",
        ),
    ])
    report = backfill.run_backfill(database, log=lambda _: None)
    assert report.rows_planned["astrometry"] > 0
    assert ("astrometry", backfill.SkipReason.LIVE_ROWS_EXIST) not in report.skipped


def test_two_kinds_of_stacking_summary_get_different_run_ids() -> None:
    """The summary kind is part of the run id."""
    document = full_document()
    ids = set()
    for kind in backfill.SUMMARY_KINDS[:2]:
        summary = backfill.find_summary(document, kind)
        assert summary is not None
        ids.add(backfill.plan_summary(TARGET, document, kind, summary).run_id)
    assert len(ids) == 2


def test_every_stacking_summary_field_is_a_measurement_or_listed_as_not_one() -> None:
    """A new stacking summary field needs a metric or a reason."""
    not_measurements = {
        "is_spectral": "a yes/no label of the stack kind",
        "excluded_frames": "a list of excluded frames",
        "rejected_fraction_flagged": "a verdict",
        "background_split_detected": "a verdict",
        "background_split_detail": "text",
        "calibration_mismatch_flags": "a list of flag sentences",
        "flat_calibration_issues": "a list of sentences",
        "saturation_flagged": "a verdict",
        "exposure_groups": "a list; the group metrics hold each group's numbers",
        "recommended_exposure_seconds": "a recommendation, not a measurement",
        "zero_fraction_flagged": "a verdict",
        "negative_pixels_flagged": "a verdict",
        "fwhm_degraded": "a verdict",
        "spectral_registration_flags": "a list of flagged frames",
        "spectral_registration_checked": "a yes/no fact; the spectral-registration gate holds it",
        "timed_out": "a verdict",
        "debayer_applied": "a yes/no setting",
        "stacking_engine": "text",
        "stacking_engine_version": "text",
        "registration_reference_frame": "a file name",
    }
    fields = set(StackingPipelineQualityMetrics.model_fields)
    mapped = set(stacking_record._METRICS_FIELDS)
    assert fields == mapped | set(not_measurements)
    assert not mapped & set(not_measurements)


def test_a_filled_history_reconciles_with_a_stored_json_document(tmp_path: Path) -> None:
    """Fill from a stored JSON file, then check each number against its row."""
    fixture = Path(__file__).parent / "data" / "stored_target_document.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    database = make_database(tmp_path, {TARGET: document})
    report = backfill.run_backfill(
        database, apply=True, make_backup=fake_backup(database), log=lambda _: None
    )
    assert report.mismatches == []
    rows = all_rows(database)
    by_metric: dict[str, list[Any]] = {}
    for row in rows:
        by_metric.setdefault(row.metric, []).append(row)
    pipelines_seen = set()
    for kind in backfill.SUMMARY_KINDS:
        summary = backfill.find_summary(document, kind)
        assert summary is not None
        pipelines_seen.add(kind.key)
        for mapping in kind.values:
            stored = backfill.as_number(backfill.first_present(summary, mapping.paths))
            if stored is None:
                continue
            candidates = by_metric.get(mapping.metric, [])
            assert candidates, f"{kind.key}: {mapping.metric} was not stored"
            assert any(row.value == pytest.approx(stored / mapping.divisor) for row in candidates), (
                mapping.metric
            )
    assert pipelines_seen == {kind.key for kind in backfill.SUMMARY_KINDS}
