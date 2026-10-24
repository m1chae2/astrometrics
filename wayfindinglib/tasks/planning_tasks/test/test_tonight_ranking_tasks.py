"""Purpose: Tests for tonight's ranking of candidate targets.

Description: Builds a made-up science library (targets, frame measurements and
stack runs) and checks the ranking: gain per clock hour with overheads, the
tonight sky model, separate lists per use, targets with no data marked new, a
stopped target ranked at zero, a note when a thin dataset leads, and targets
that are never ranked or are not visible. The library is a small stand-in
with the same ``targets`` and ``quality`` calls the real one has.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import (
    CaptureSetting,
    ConfigurationError,
    FrameRecord,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
    Target,
    frame_subject_id,
)
from wayfindinglib.analytics.result_quality import forecast as fc
from wayfindinglib.analytics.result_quality.capture_overheads import CaptureOverheads
from wayfindinglib.analytics.result_quality.efficiency import (
    READ_NOISE_METRIC,
    SKY_NOISE_METRIC,
    SKY_RATE_METRIC,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
    baseline_key,
    frame_observations_from_measurements,
)
from wayfindinglib.analytics.result_quality.sky_model import SkyModel
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.tonight_ranking import CandidateStatus, RankedUse
from wayfindinglib.tasks.planning_tasks import tonight_ranking_tasks as tr

SITE = SiteProfile(id="site", name="Home", latitude_deg=45.76, longitude_deg=-110.74, elevation_m=1400.0)
SETTING = CaptureSetting(
    camera="CamA",
    telescope="Scope",
    filter_name="L",
    exposure_seconds=60.0,
    gain_setting=100,
    offset=10,
    binning=1,
)
TONIGHT = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
FIRST_NIGHT = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
M81 = ("M 81", "09h 55m 33s", "+69° 03′ 55″")
NGC_7331 = ("NGC 7331", "22h 37m 04s", "+34° 24′ 56″")
NGC_104 = ("NGC 104", "00h 24m 05s", "-72° 04′ 52″")
OVERHEADS = CaptureOverheads(
    download_seconds=5.0,
    dither_settle_seconds=20.0,
    autofocus_seconds=120.0,
    download_from_records=False,
    dither_from_records=False,
    autofocus_from_records=False,
    gaps_used=0,
)


def _target(spec: tuple[str, str, str], frame_count: int, nights: int = 1, **extra: Any) -> Target:
    """Build a library target with light frames.

    Parameters
    ----------
    spec : `tuple` [`str`, `str`, `str`]
        The target id, right ascension and declination.
    frame_count : `int`
        Frames on each night.
    nights : `int`, optional
        How many nights.
    **extra
        Other fields of the target.

    Returns
    -------
    target : `Target`
        The target.
    """
    target_id, ra, dec = spec
    frames = []
    for night in range(nights):
        for index in range(frame_count):
            moment = FIRST_NIGHT + timedelta(days=night, minutes=2 * index)
            frames.append(
                FrameRecord(
                    path=f"/lib/lights/{target_id}/Scope/CamA/n{night}_{index:03d}.fits",
                    timestamp=moment.timestamp(),
                    exposure="60",
                )
            )
    return Target(id=target_id, ra=ra, dec=dec, frames=frames, **extra)


def _frame_rows(target: Target, sky: float = 0.5, width: float = 3.0) -> list[QualityMeasurement]:
    """Build the four measurements of every frame of a target.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The rows.
    """
    rows = []
    for frame in target.frames:
        subject = QualitySubject(
            level=SubjectLevel.FRAME,
            subject_id=frame_subject_id(frame.path, target.id),
            target_id=target.id,
            observing_night_id=datetime.fromtimestamp(frame.timestamp or 0.0, tz=UTC).date().isoformat(),
            capture_setting_id=1,
        )
        for metric, version, value in (
            (STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 1, width),
            (SKY_RATE_METRIC, 2, sky),
            (SKY_NOISE_METRIC, 2, (sky * 60.0) ** 0.5),
            (READ_NOISE_METRIC, 1, 3.0),
        ):
            rows.append(
                QualityMeasurement(
                    metric=metric,
                    definition_version=version,
                    subject=subject,
                    status=MeasurementStatus.MEASURED,
                    value=value,
                    origin=MeasurementOrigin.MEASURED,
                    run_id="frames",
                    measured_at=FIRST_NIGHT,
                )
            )
    return rows


def _stack_rows(target: Target, measured: list[float], expected: list[float]) -> list[QualityMeasurement]:
    """Build stack runs holding every frame of a target.

    Parameters
    ----------
    target : `Target`
        The target.
    measured, expected : `list` [`float`]
        The measured and expected noise of each run.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The stack and weight rows.
    """
    rows = []
    members = [frame_subject_id(frame.path, target.id) for frame in target.frames]
    for index, (noise, expect) in enumerate(zip(measured, expected, strict=True)):
        when = FIRST_NIGHT + timedelta(days=index)
        stack = QualitySubject(level=SubjectLevel.STACK, subject_id=f"{target.id}/stack", target_id=target.id)
        for metric, version, value in (
            (fc.NORMALIZED_NOISE_METRIC, 3, noise / 1000.0),
            (fc.NOISE_METRIC, 3, noise),
            (fc.EXPECTED_NOISE_METRIC, 1, expect),
        ):
            rows.append(
                QualityMeasurement(
                    metric=metric,
                    definition_version=version,
                    subject=stack,
                    status=MeasurementStatus.MEASURED,
                    value=value,
                    sample_count=1000,
                    origin=MeasurementOrigin.MEASURED,
                    run_id=f"run{index}",
                    measured_at=when,
                    row_id=100 * (index + 1),
                )
            )
        for member in members:
            rows.append(
                QualityMeasurement(
                    metric=fc.FRAME_WEIGHT_METRIC,
                    definition_version=1,
                    subject=QualitySubject(level=SubjectLevel.FRAME, subject_id=member, target_id=target.id),
                    status=MeasurementStatus.MEASURED,
                    value=1.0,
                    origin=MeasurementOrigin.MEASURED,
                    run_id=f"run{index}",
                    measured_at=when,
                )
            )
    return rows


class FakeLibrary:
    """A stand-in for the science library's ``targets`` and ``quality``."""

    def __init__(self, targets: list[Target], rows: list[QualityMeasurement]) -> None:
        """Keep the targets and the stored rows.

        Parameters
        ----------
        targets : `list` [`Target`]
            The library targets.
        rows : `list` [`QualityMeasurement`]
            Every stored measurement.
        """
        self.rows = rows
        self.targets = SimpleNamespace(list=lambda: targets)
        self.quality = SimpleNamespace(query=self._query, capture_settings=lambda: {1: SETTING})

    def _query(
        self,
        metric: str | None = None,
        level: str | None = None,
        target_id: str | None = None,
        **_: Any,
    ) -> list[QualityMeasurement]:
        """Answer ``quality.query`` from the stored rows.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The rows that match the metric, level and target.
        """
        return [
            row
            for row in self.rows
            if (metric is None or row.metric == metric)
            and (level is None or row.subject.level.value == level)
            and (target_id is None or row.subject.target_id == target_id)
        ]


def _rank(library: FakeLibrary, **extra: Any) -> Any:
    """Rank tonight's candidates at the test site.

    Returns
    -------
    ranking : `TonightRanking`
        The ranking.
    """
    return tr.rank_tonight(TONIGHT, astrometrics=library, site_profile=SITE, ekos_contexts=[], **extra)


def _library() -> FakeLibrary:
    """Build a library with several kinds of target.

    Returns
    -------
    library : `FakeLibrary`
        M 81 with 8 hours of frames, NGC 7331 with 20 minutes, a target with no
        frames, a planet, and a target that never rises at the site.
    """
    deep = _target(M81, 30, nights=16)
    thin = _target(NGC_7331, 20)
    new = Target(id="NGC 6946", ra="20h 34m 52s", dec="+60° 09′ 14″")
    planet = Target(id="Jupiter", ra="05h 00m 00s", dec="+22° 00′ 00″")
    south = _target(NGC_104, 5)
    rows = (
        _frame_rows(deep)
        + _frame_rows(thin)
        + _frame_rows(south)
        + _stack_rows(deep, [4.0], [3.0])
        + _stack_rows(thin, [4.0], [3.0])
    )
    return FakeLibrary([deep, thin, new, planet, south], rows)


def test_targets_are_ranked_by_gain_with_new_ones_unranked() -> None:
    """Ranked targets come first; the new one has no rank."""
    ranking = _rank(_library())

    (imaging,) = ranking.lists
    assert imaging.use == RankedUse.IMAGING
    by_id = {c.target_id: c for c in imaging.candidates}
    assert set(by_id) == {"M 81", "NGC 7331", "NGC 6946"}
    assert by_id["NGC 6946"].status == CandidateStatus.NEW
    assert by_id["NGC 6946"].rank is None
    assert by_id["M 81"].rank == 2
    assert by_id["NGC 7331"].rank == 1
    assert (by_id["NGC 7331"].gain_per_hour_fraction or 0.0) > (by_id["M 81"].gain_per_hour_fraction or 0.0)
    assert imaging.candidates[-1].target_id == "NGC 6946"
    assert any("solar-system" in note for note in ranking.notes)
    assert any("not visible" in note for note in ranking.notes)


def test_a_thin_dataset_that_leads_is_called_out() -> None:
    """The short history of NGC 7331 leads, and a note says why."""
    ranking = _rank(_library())

    leader = ranking.lists[0].candidates[0]
    assert leader.target_id == "NGC 7331"
    assert leader.thin_dataset
    assert not next(c for c in ranking.lists[0].candidates if c.target_id == "M 81").thin_dataset
    assert any("NGC 7331 leads" in note and "thin" in note for note in ranking.notes)


def test_an_hour_is_counted_in_clock_time_with_overheads() -> None:
    """Downloads, dithers and one autofocus run shorten the hour's frames."""
    ranking = _rank(_library())

    candidate = ranking.lists[0].candidates[0]
    # (3600 - 120 s autofocus) / (60 s + 5 s download + 20 s / 3 dither) = 48.
    assert candidate.frames_in_hour == 48
    assert candidate.exposure_seconds == pytest.approx(60.0)
    assert candidate.overhead_fraction is not None and candidate.overhead_fraction > 0.2


def test_the_sky_comes_from_the_history_without_a_model_and_says_so() -> None:
    """With too few nights no sky model is made, and the sky label says so."""
    target = _target(NGC_7331, 20, nights=2)
    ranking = _rank(FakeLibrary([target], _frame_rows(target)))

    candidate = ranking.lists[0].candidates[0]
    assert candidate.sky_source == "history"
    assert "No sky model" in candidate.reason
    assert ranking.sky.model_derived
    assert ranking.sky.confidence.value == "design_estimate"
    assert ranking.sky.unavailable


def test_a_fitted_sky_model_is_used_for_tonight() -> None:
    """With enough nights the sky model sets the sky rate of the new frames."""
    library = _library()
    deep = next(t for t in library.targets.list() if t.id == "M 81")

    ranking = _rank(library)
    candidate = {c.target_id: c for c in ranking.lists[0].candidates}["M 81"]

    assert candidate.sky_source == "model"
    assert candidate.sky_rate_e_per_s_per_px == pytest.approx(0.5, rel=0.05)
    assert ranking.sky.settings[0].nights_used == 16
    assert ranking.sky.moon_illumination_fraction is not None
    assert deep.frames


def test_a_brighter_sky_lowers_the_gain() -> None:
    """A brighter modeled sky gives the same target less gain per hour."""
    target = _target(M81, 30, nights=2)
    observations_rows = _frame_rows(target)
    runs = fc.stack_runs_from_measurements(_stack_rows(target, [4.0], [3.0]))
    observations = frame_observations_from_measurements(observations_rows, {1: SETTING})
    key = baseline_key(SETTING)

    def gain(constant: float) -> float:
        """Score the target under a sky model with a constant sky rate.

        Returns
        -------
        gain : `float`
            The gain per hour.
        """
        model = SkyModel(
            setting_key=key,
            constant=constant,
            airmass_coefficient=0.0,
            moon_coefficient=0.0,
            moon_fitted=True,
            nights_used=6,
            frames_used=60,
            in_sample_relative_error=0.05,
            held_out_relative_error=0.1,
        )
        candidate = tr.score_candidate(
            "M 81",
            RankedUse.IMAGING,
            observations,
            runs,
            epoch_seconds=TONIGHT.timestamp(),
            direction=(148.89, 69.07),
            altitude_deg=50.0,
            site=SITE,
            sky_models={key: model},
            overheads_for=lambda setting: OVERHEADS,
        )
        assert candidate.sky_source == "model"
        return candidate.gain_per_hour_fraction or 0.0

    assert gain(0.5) > gain(5.0)


def test_a_target_past_its_stop_signal_is_ranked_at_zero() -> None:
    """A floor fitted over several stacks ranks the target at zero."""
    stopped = _target(M81, 10)
    healthy = _target(NGC_7331, 10)
    expected = [8.0, 4.0, 2.0, 1.0]
    measured = [(e**2 + 36.0) ** 0.5 for e in expected]
    rows = (
        _frame_rows(stopped)
        + _frame_rows(healthy)
        + _stack_rows(stopped, measured, expected)
        + _stack_rows(healthy, [4.0], [3.0])
    )

    ranking = _rank(FakeLibrary([stopped, healthy], rows))

    first, second = ranking.lists[0].candidates
    assert (first.target_id, first.status) == ("NGC 7331", CandidateStatus.RANKED)
    assert (second.target_id, second.status) == ("M 81", CandidateStatus.STOPPED)
    assert second.gain_per_hour_fraction == pytest.approx(0.0)
    assert second.rank == 2
    assert "floor" in second.reason


def test_a_target_with_frames_but_no_stack_is_forecast_from_its_frames() -> None:
    """A target never stacked is ranked from its frames, with a remark."""
    target = _target(M81, 10)

    ranking = _rank(FakeLibrary([target], _frame_rows(target)))

    (candidate,) = ranking.lists[0].candidates
    assert candidate.status == CandidateStatus.RANKED
    assert "no stack yet" in candidate.reason


def test_a_frame_without_read_noise_gives_no_forecast_with_a_reason() -> None:
    """Missing read noise makes a candidate with no forecast, not a guess."""
    target = _target(M81, 10)
    rows = [row for row in _frame_rows(target) if row.metric != READ_NOISE_METRIC]

    ranking = _rank(FakeLibrary([target], rows))

    (candidate,) = ranking.lists[0].candidates
    assert candidate.status == CandidateStatus.NO_FORECAST
    assert candidate.rank is None
    assert candidate.reason


def test_lists_are_kept_apart_by_use() -> None:
    """Spectroscopy and photometry targets have lists of their own."""
    spectral = _target(M81, 5)
    for frame in spectral.frames:
        frame.filter = "Star Analyzer 200"
    imaging = _target(NGC_7331, 5)
    rows = _frame_rows(spectral) + _frame_rows(imaging)

    ranking = _rank(FakeLibrary([spectral, imaging], rows))

    assert [ranked.use for ranked in ranking.lists] == [RankedUse.IMAGING, RankedUse.SPECTROSCOPY]
    assert ranking.lists[1].candidates[0].target_id == "M 81"
    assert "spectral" in ranking.lists[1].gain_description


def test_asteroid_searches_are_never_ranked() -> None:
    """A target with asteroid-search results is left out."""
    target = _target(M81, 5)
    target.asteroid_detection.candidates = [SimpleNamespace(cascade_stage=SimpleNamespace(value="x"))]  # type: ignore[list-item]

    assert tr.classify_use(target) is None


def test_asking_again_with_a_later_time_reranks() -> None:
    """The ranking is recomputed for the new time: altitude changes."""
    library = _library()

    early = tr.rank_tonight(TONIGHT, astrometrics=library, site_profile=SITE, ekos_contexts=[])
    late = tr.rank_tonight(
        TONIGHT + timedelta(hours=4), astrometrics=library, site_profile=SITE, ekos_contexts=[]
    )

    altitude_early = {c.target_id: c.altitude_deg for c in early.lists[0].candidates}
    altitude_late = {c.target_id: c.altitude_deg for c in late.lists[0].candidates}
    assert altitude_early["M 81"] != altitude_late["M 81"]
    assert late.at - early.at == timedelta(hours=4)


def test_a_missing_site_without_a_butler_is_refused() -> None:
    """Ranking needs a site, and says so when it has none."""
    with pytest.raises(ConfigurationError):
        tr.rank_tonight(TONIGHT, astrometrics=_library())


def test_a_long_ranking_leaves_out_its_lowest_candidates_to_fit_the_reply_budget() -> None:
    """120 made-up targets give a ranking that fits, the best kept."""
    from wayfindinglib.analytics.result_quality.reply_budget import MAXIMUM_REPLY_CHARACTERS, reply_characters
    from wayfindinglib.models.planning.tonight_ranking import RankedCandidate, RankedList, TonightRanking

    now = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    candidates = [
        RankedCandidate(
            target_id=f"NGC {1000 + index}",
            use=RankedUse.IMAGING,
            status=CandidateStatus.RANKED,
            rank=index + 1,
            gain_per_hour_fraction=0.5 / (index + 1),
            reason="The hour copies the target's past frames at tonight's modeled sky. " * 3,
        )
        for index in range(120)
    ]
    ranking = TonightRanking(
        at=now,
        lists=[RankedList(use=RankedUse.IMAGING, gain_description="noise", candidates=candidates)],
        sky=tr.summarize_sky(now.timestamp(), SITE, {}, []),
    )
    assert reply_characters(ranking) > MAXIMUM_REPLY_CHARACTERS
    fitted = tr.fit_ranking_to_budget(ranking)
    assert reply_characters(fitted) <= MAXIMUM_REPLY_CHARACTERS
    kept = fitted.lists[0].candidates
    assert [candidate.target_id for candidate in kept] == [c.target_id for c in candidates[: len(kept)]]
    assert any("left out" in note for note in fitted.notes)
    assert tr.fit_ranking_to_budget(fitted) is fitted
