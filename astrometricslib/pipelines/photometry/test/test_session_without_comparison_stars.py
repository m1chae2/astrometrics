"""Purpose: Test what happens to a session that has no comparison stars.

Description: On 2026-09-24 most M 27 frames lost their stars to cloud, so no
star had a flux in every frame and the session's comparison pool was empty.
Its light curves kept raw flux and were merged with other nights' normalized
flux, which made many stars look variable. These tests check that:

* the analyzer says why a session could not be normalized, in a sentence
  that counts the stars left after each rule;
* the runner drops such a session's variable candidates, leaves its light
  curves out of the merge when another session was normalized, and lists
  the session in the payload;
* every other warning logged during the run is kept for the job, without
  repeating the warnings that have their own notice row;
* `validate_output` stores both as notice rows that `Jobs.query` lists.
"""

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_NOTICE_LOGGED_WARNINGS,
    PHOTOMETRY_NOTICE_SESSION_WITHOUT_COMPARISON_STARS,
)
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.run_warnings import (
    SESSION_NOTICE_LOG_EXTRA,
    LoggedWarning,
    SessionWithoutComparisonStars,
    collect_logged_warnings,
)
from astrometricslib.pipelines.photometry.test.post_processing.test_runner_science_outcomes import (
    _store_and_request,
)
from astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection import (
    FRAME_COUNT,
    analyze,
    simulate_session,
)
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result

DRIVER_LOGGER = logging.getLogger("astrometricslib._internal.drivers.test_catalog_driver")


def test_a_session_where_every_star_misses_a_frame_is_still_normalized() -> None:
    """One missing frame in 36 is inside the 95% share, so the set is built."""
    star_count = 60
    session = simulate_session(
        star_count=star_count, missing_in_frame={index: index % FRAME_COUNT for index in range(star_count)}
    )

    analyzer = analyze(session)

    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) >= 5
    assert analyzer.normalization_failure_reason is None


def test_a_normalized_session_has_no_failure_reason() -> None:
    """A healthy session is normalized and records no reason."""
    analyzer = analyze(simulate_session(seed=3))

    assert analyzer.normalization_failure_reason is None
    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) >= 2


def _fake_analyzer(star_ids: list[str], failure_reason: str | None) -> SimpleNamespace:
    """Build the parts of a `VariabilityAnalyzer` that the runner reads.

    Parameters
    ----------
    star_ids : `list` [`str`]
        The ids of the session's stars.
    failure_reason : `str` or `None`
        The session's `normalization_failure_reason`.

    Returns
    -------
    analyzer : `types.SimpleNamespace`
        A stand-in analyzer with one-point light curves.
    """
    stars = []
    for star_id in star_ids:
        star = StellarObject(id=star_id)
        star.photometry = PhotometryResult(
            timestamps=[datetime(2026, 9, 24, 3, 0)],
            fluxes=[1.0],
            mean_flux=1.0,
            coefficient_of_variation=0.1,
        )
        stars.append(star)
    return SimpleNamespace(
        stellar_objects=stars,
        normalization_failure_reason=failure_reason,
        frames_without_usable_date_obs=[],
        rejected_files=[],
        frame_ensemble_composition=[],
        comparison_set=None,
        session_zero_points=None,
        centroid_shift_summary=lambda: None,
        frame_exposure_s={},
        detector_noise=DetectorNoise(),
        scintillation=None,
        scintillation_fraction_by_frame={},
    )


def _run_two_sessions(
    monkeypatch: pytest.MonkeyPatch, failure_reasons: tuple[str | None, str | None]
) -> tuple[Result, list[list[str]], list[str]]:
    """Run the runner on two stand-in sessions.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the session analysis, the merge and the saving.
    failure_reasons : `tuple`
        Each session's `normalization_failure_reason`.

    Returns
    -------
    result, merged_session_ids, long_term_calls : `tuple`
        The run's result, the ids of the sessions handed to each merge
        call, and one entry per call of the long-term candidate search.
    """
    sessions = [SimpleNamespace(id=f"night-{index}", frame_paths=[f"/f{index}.fits"]) for index in (1, 2)]
    analyzers = {
        session.id: _fake_analyzer([f"{session.id}:Star_{n}" for n in range(3)], reason)
        for session, reason in zip(sessions, failure_reasons, strict=True)
    }

    def fake_session_analysis(session: SimpleNamespace, *_arguments: object, **_options: object) -> tuple:
        """Return the stand-in analyzer and one candidate, and log one warning.

        Returns
        -------
        analysis : `tuple`
            The analyzer, its candidates and no identification result.
        """
        DRIVER_LOGGER.warning("Catalog server %s could not be reached; trying the other one.", "primary")
        analyzer = analyzers[session.id]
        return analyzer, [analyzer.stellar_objects[0]], None

    merged_session_ids: list[list[str]] = []

    def fake_merge(
        photometry_sessions: list, per_session_results: list, *_arguments: object, **_options: object
    ) -> tuple[list, list, int]:
        """Record which sessions were merged and return their stars.

        Returns
        -------
        merged : `tuple`
            The stars, no session without a sky map, and one match.
        """
        merged_session_ids.append([session.id for session in photometry_sessions])
        stars = [star for analyzer, _ in per_session_results for star in analyzer.stellar_objects]
        return stars, [], 1

    long_term_calls: list[str] = []

    def fake_long_term(stars: list) -> list:
        """Record that the long-term search ran.

        Returns
        -------
        candidates : `list`
            No candidates.
        """
        long_term_calls.append("called")
        return []

    from astrometricslib.pipelines.photometry.processing import variability_analyzer

    monkeypatch.setattr(runner, "_run_variability_analysis_for_session", fake_session_analysis)
    monkeypatch.setattr(runner, "_match_and_merge_across_sessions", fake_merge)
    monkeypatch.setattr(variability_analyzer, "identify_long_term_variable_candidates", fake_long_term)
    monkeypatch.setattr(runner, "record_pipeline_stars", lambda stars, **_options: (stars, None))
    monkeypatch.setattr(runner, "search_periods_and_save", lambda *_arguments: 0)
    monkeypatch.setattr(runner, "label_known_variability", lambda *_arguments, **_keywords: None)

    request = PipelineRequest(
        target=SimpleNamespace(id="M 27", frames=[]),
        catalog_access=None,
        options={"use_astrometry_seed": False, "max_workers": 1},
    )
    start = Result(
        payload={
            "photometry_sessions": sessions,
            "image_paths": [],
            "photometry_frames_without_timestamp": [],
        }
    )
    return runner.PhotometryPipelineAdapter().run(request, start), merged_session_ids, long_term_calls


def test_a_session_without_comparison_stars_is_left_out_of_the_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw flux is never merged with another night's normalized flux."""
    result, merged_session_ids, long_term_calls = _run_two_sessions(
        monkeypatch, (None, "0 of 3 stars have a positive flux in all 33 usable frames")
    )

    assert merged_session_ids == [["night-1"]]
    assert long_term_calls == ["called"]
    assert [star.id for star in result.candidates] == ["night-1:Star_0"]
    (record,) = result.payload["sessions_without_comparison_stars"]
    assert record.session_id == "night-2"
    assert record.excluded_from_merge is True
    assert record.dropped_candidate_count == 1
    assert "0 of 3 stars" in record.reason


def test_when_no_session_has_comparison_stars_the_raw_sessions_are_merged_but_not_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All sessions raw: all are merged, with no search between nights."""
    result, merged_session_ids, long_term_calls = _run_two_sessions(monkeypatch, ("cloud", "cloud"))

    assert merged_session_ids == [["night-1", "night-2"]]
    assert long_term_calls == []
    assert result.candidates == []
    assert all(
        not record.excluded_from_merge for record in result.payload["sessions_without_comparison_stars"]
    )


def test_the_run_keeps_the_warnings_it_logged_for_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated warning is kept once, counted; notices are skipped."""
    result, _, _ = _run_two_sessions(monkeypatch, (None, "cloud"))

    logged = result.payload["logged_warnings"]
    driver_warnings = [warning for warning in logged if warning.logger_name == DRIVER_LOGGER.name]
    assert len(driver_warnings) == 1
    assert driver_warnings[0].count == 2
    assert "primary could not be reached" in driver_warnings[0].message
    assert not any("no comparison stars" in warning.message for warning in logged)


def test_the_collector_groups_skips_tagged_records_and_stops_at_the_end() -> None:
    """Tagged records are skipped, and nothing is kept after the run."""
    with collect_logged_warnings() as collector:
        for frame in range(3):
            DRIVER_LOGGER.warning("Frame %s has no gain entry.", frame)
        DRIVER_LOGGER.warning("Session has no comparison stars.", extra=SESSION_NOTICE_LOG_EXTRA)
        DRIVER_LOGGER.info("An ordinary step.")
    DRIVER_LOGGER.warning("Logged after the run.")

    assert collector.warnings() == [
        LoggedWarning(logger_name=DRIVER_LOGGER.name, message="Frame 0 has no gain entry.", count=3)
    ]


def test_validate_output_stores_both_notices_for_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both notices are stored as failed rows of the job."""
    store, request = _store_and_request(tmp_path, monkeypatch)
    result = runner._empty_photometry_result("no frames for the requested filter")
    result.payload["sessions_without_comparison_stars"] = [
        SessionWithoutComparisonStars("night-2", "0 of 3 stars have a positive flux", True, 4)
    ]
    result.payload["logged_warnings"] = [
        LoggedWarning(
            "astrometricslib._internal.drivers.astroquery_simbad_driver", "SIMBAD server failed", 1
        ),
        LoggedWarning(
            "astrometricslib.pipelines.photometry.batch", "Period search failed: Singular matrix", 2
        ),
    ]

    runner.PhotometryPipelineAdapter().validate_output(request, result)

    rows = {row.metric: row for row in store.query(job_id="job-11", latest_only=False)}
    session_row = rows[PHOTOMETRY_NOTICE_SESSION_WITHOUT_COMPARISON_STARS]
    assert session_row.value == pytest.approx(1.0)
    assert session_row.check_outcome.value == "failed"
    assert "night-2" in session_row.reason
    assert "left 1 session(s) out of the merged light curves" in session_row.action_taken
    warnings_row = rows[PHOTOMETRY_NOTICE_LOGGED_WARNINGS]
    assert warnings_row.value == pytest.approx(2.0)
    assert warnings_row.check_outcome.value == "failed"
    assert "astroquery_simbad_driver: SIMBAD server failed" in warnings_row.reason
    assert "Singular matrix (2 times)" in warnings_row.reason


def test_a_run_without_notices_stores_no_notice_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A notice is stored only when it happened."""
    store, request = _store_and_request(tmp_path, monkeypatch)

    runner.PhotometryPipelineAdapter().validate_output(
        request, runner._empty_photometry_result("no frames for the requested filter")
    )

    metrics = {row.metric for row in store.query(job_id="job-11", latest_only=False)}
    assert PHOTOMETRY_NOTICE_SESSION_WITHOUT_COMPARISON_STARS not in metrics
    assert PHOTOMETRY_NOTICE_LOGGED_WARNINGS not in metrics
