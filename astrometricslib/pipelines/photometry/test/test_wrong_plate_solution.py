"""Purpose: Test that a session with a wrong plate solution saves no stars.

Description: On the M 31 photometry run of 2026-11-04 the session's map came
from the mount's pointing, about 56 arcseconds off, so none of its stars met
their catalog star and 303 false position-only stars were saved. These tests
check that the runner leaves such a session out of the merge and saves
nothing when no session has a solution to trust, and that a Nikon session
does not borrow the plate solution of another camera's stack.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from astrometricslib.models.quality.astrometry_quality import CatalogMatchQuality
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.shared.session_identification import plate_solution_problem


def _identified_stars(matched: int, total: int, offset_arcsec: float = 0.5) -> list[StellarObject]:
    """Build a session's identified stars, some matched to a catalog.

    Returns
    -------
    stars : `list` [`StellarObject`]
        ``total`` stars at a position, the first ``matched`` of them matched
        to a catalog star ``offset_arcsec`` away.
    """
    stars = []
    for index in range(total):
        star = StellarObject(id=f"FIELD_J{index}", right_ascension=10.0 + index * 0.01, declination=41.0)
        if index < matched:
            star.id = f"Gaia DR3 {index + 1}"
            star.is_catalog_identified = True
            star.catalog_match_quality = CatalogMatchQuality(
                matched_via="gaia", separation_arcsec=offset_arcsec
            )
        stars.append(star)
    return stars


def _fake_analyzer(session_id: str) -> SimpleNamespace:
    """Build the parts of a `VariabilityAnalyzer` that the runner reads.

    Returns
    -------
    analyzer : `types.SimpleNamespace`
        A stand-in analyzer with three one-point light curves.
    """
    stars = []
    for index in range(3):
        star = StellarObject(id=f"Gaia DR3 {session_id}{index}")
        star.photometry = PhotometryResult(
            timestamps=[datetime(2026, 11, 4, 3, 0)], fluxes=[1.0], mean_flux=1.0
        )
        stars.append(star)
    return SimpleNamespace(
        stellar_objects=stars,
        normalization_failure_reason=None,
        frames_without_usable_date_obs=[],
        rejected_files=[],
        frame_ensemble_composition=[],
        comparison_set=None,
        session_zero_points=None,
        centroid_shift_summary=lambda: None,
        frame_exposure_seconds={},
        detector_noise=DetectorNoise(),
        scintillation=None,
        scintillation_fraction_by_frame={},
    )


def _run(monkeypatch: pytest.MonkeyPatch, solutions_wrong: list[bool]) -> tuple[list, list, list]:
    """Run the runner on stand-in sessions, each with a right or wrong map.

    Returns
    -------
    saved_calls, merged_session_ids, period_calls : `tuple`
        The stars handed to each save, the sessions handed to each merge,
        and one entry per period search.
    """
    sessions = [
        SimpleNamespace(id=f"night-{index}", frame_paths=[f"/night-{index}/frame_001.fits"])
        for index in range(1, len(solutions_wrong) + 1)
    ]
    wrong_by_id = {session.id: wrong for session, wrong in zip(sessions, solutions_wrong, strict=True)}

    def fake_session_analysis(session: SimpleNamespace, *_arguments: object, **_options: object) -> tuple:
        """Return a stand-in analyzer and identification result.

        Returns
        -------
        analysis : `tuple`
            The analyzer, no candidates and the identification result.
        """
        matched = 0 if wrong_by_id[session.id] else 20
        identify_result = SimpleNamespace(
            wcs=object(),
            stellar_objects=_identified_stars(matched, 30),
            nearest_catalog_separations_arcsec=[0.5] * matched,
            catalog_density_per_square_degree=7700.0,
            simbad_matched_count=0,
            reused_existing_header_wcs=True,
            header_wcs_replaced_after_verification=False,
        )
        return _fake_analyzer(session.id), [], identify_result

    merged_session_ids: list[list[str]] = []

    def fake_merge(sessions_merged: list, results: list, *_arguments: object, **_options: object) -> tuple:
        """Record which sessions were merged and return their stars.

        Returns
        -------
        merged : `tuple`
            The stars, no session without a sky map, and no match.
        """
        merged_session_ids.append([session.id for session in sessions_merged])
        return [star for analyzer, _ in results for star in analyzer.stellar_objects], [], 0

    saved_calls: list[list[str]] = []

    def fake_record(stars: list, **_options: object) -> tuple:
        """Record the stars handed to the save.

        Returns
        -------
        recorded : `tuple`
            The stars and no breakdown.
        """
        saved_calls.append([star.id for star in stars])
        return stars, None

    period_calls: list[str] = []
    monkeypatch.setattr(runner, "_run_variability_analysis_for_session", fake_session_analysis)
    monkeypatch.setattr(runner, "_match_and_merge_across_sessions", fake_merge)
    monkeypatch.setattr(runner, "record_pipeline_stars", fake_record)
    monkeypatch.setattr(runner, "search_periods_and_save", lambda *_arguments: period_calls.append("called"))
    monkeypatch.setattr(runner, "label_known_variability", lambda *_arguments, **_keywords: None)

    request = PipelineRequest(
        target=SimpleNamespace(id="M 31", frames=[]),
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
    runner.PhotometryPipelineAdapter().run(request, start)
    return saved_calls, merged_session_ids, period_calls


def test_a_session_with_a_wrong_solution_is_left_out_of_the_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the well-solved session's stars are merged and saved."""
    saved_calls, merged_session_ids, _period_calls = _run(monkeypatch, [False, True])

    assert merged_session_ids == [["night-1"]]
    assert len(saved_calls) == 1
    assert all(star_id.startswith("Gaia DR3 night-1") for star_id in saved_calls[0])


def test_no_star_is_saved_when_the_only_solution_is_wrong(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A lone session with a wrong map saves nothing and says why."""
    saved_calls, _merged_session_ids, period_calls = _run(monkeypatch, [True])

    assert saved_calls == []
    assert period_calls == []
    assert "/night-1/frame_001.fits" in caplog.text
    assert "looks wrong" in caplog.text


def test_the_plate_solution_judge() -> None:
    """A good map passes, few or scattered matches fail, small fields pass.

    The default match radius is 10 arcsec and the search circle 20 arcsec.
    The catalog density is not known, so the sparse-field chance share is
    used.
    """
    good_neighbors = [0.5] * 20 + [15.0] * 10
    assert plate_solution_problem(_identified_stars(20, 30), good_neighbors, None) is None
    assert "matched a catalog star" in plate_solution_problem(_identified_stars(1, 30), good_neighbors, None)
    scattered_neighbors = [5.0] * 8 + [15.0] * 22
    assert "match radius" in plate_solution_problem(_identified_stars(8, 30), scattered_neighbors, None)
    assert plate_solution_problem(_identified_stars(0, 10), [], None) is None


def test_a_session_borrows_only_the_stack_of_its_own_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame with only a pointing map takes the solved stack of its setup.

    `resolve_solved_stack_wcs` matches the frame's camera and focal length.
    When it finds no such stack, the frame keeps no borrowed map, and the
    pointing map in its header is not trusted.
    """
    from astrometricslib._internal.drivers import image as image_module
    from astrometricslib.pipelines.photometry import batch
    from astrometricslib.pipelines.shared import session_identification, target_center_hint

    class _StopError(Exception):
        """Ends the run once the identification call has been seen."""

    reference = SimpleNamespace(wcs="pointing map", header={"CTYPE1": "RA---TAN"})
    monkeypatch.setattr(image_module, "AstrometricsImage", lambda _path: reference)
    monkeypatch.setattr(batch, "resolve_target_center_hint", lambda _target: (None, None))
    seen: dict[str, object] = {}

    def fake_identify(image: SimpleNamespace, _identifier: object, **options: object) -> None:
        """Note the map and the trust flag, then stop.

        Raises
        ------
        _StopError
            Always.
        """
        seen["wcs"] = image.wcs
        seen["trust"] = options.get("trust_image_wcs")
        raise _StopError

    monkeypatch.setattr(session_identification, "identify_session_stars", fake_identify)
    session = SimpleNamespace(id="night-1", frame_paths=["/nikon/frame_001.fits"])
    target = SimpleNamespace(id="M 31")

    monkeypatch.setattr(target_center_hint, "resolve_solved_stack_wcs", lambda _target, _path: None)
    with pytest.raises(_StopError):
        batch._run_variability_analysis_for_session(session, 1, "", target=target, star_identifier=object())
    assert seen == {"wcs": "pointing map", "trust": False}

    stack_wcs = SimpleNamespace(has_celestial=True)
    monkeypatch.setattr(target_center_hint, "resolve_solved_stack_wcs", lambda _target, _path: stack_wcs)
    with pytest.raises(_StopError):
        batch._run_variability_analysis_for_session(session, 1, "", target=target, star_identifier=object())
    assert seen == {"wcs": stack_wcs, "trust": True}
