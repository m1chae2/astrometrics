"""Purpose: Unit tests for gathering performance-envelope inputs.

Description: Verifies how the envelope's inputs are read: the camera's
stored profile, the star width taken from the stored measurements of the
equipment's own frames
(including which frames are left out and why), and the per-session
baseline collected only from sessions of the same equipment. The baseline
tests use a real `ControlRecordStore` on a temporary database. The last
tests check that a kept table of the nights' baselines, brought up to date
night by night (`update_night_baseline_table`), equals a full read after
live samples, a changed session record and a new guiding run, and that only
the changed nights are worked out again.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib import AppConfiguration
from astrometricslib._internal.frames import frame_is_spectral
from astrometricslib.arguments import observing_night_id
from wayfindinglib.analytics import envelope_assembly
from wayfindinglib.analytics.capture_history import STAR_WIDTH_METRIC
from wayfindinglib.analytics.envelope_assembly import (
    NightBaselineTable,
    collect_baseline_values,
    collect_excursion_fraction_baseline,
    collect_night_baselines,
    measure_image_quality,
    read_night_baseline_table,
    sensor_limits_for_camera,
    update_night_baseline_table,
)
from wayfindinglib.analytics.performance_envelope import MINIMUM_SAMPLES_PER_SESSION, MeasuredImageQuality
from wayfindinglib.analytics.test.frame_measurement_helpers import FakeAstrometrics
from wayfindinglib.drivers.control_record_store import ControlRecordStore
from wayfindinglib.models.session.ekos_session import EkosSessionContext, SessionEquipmentAttribution
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.test.camera_profile_support import library_with_camera_profiles


@pytest.fixture
def camera_profile_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shipped_camera_sections: str
) -> AppConfiguration:
    """Build a config holding the shipped camera profiles.

    Passed explicitly, so these tests do not depend on the process-wide
    configuration, which other test modules replace.

    Returns
    -------
    config : `AppConfiguration`
        A configuration with every shipped camera profile and the generic
        fallback.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    config_path.write_text(shipped_camera_sections, encoding="utf-8")
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


_frame_counter = {"next": 0}
"""Gives each fake frame its own path, since stored rows are joined by path."""


class _Frame:
    """A minimal light-frame record."""

    def __init__(
        self,
        camera: str = "ZWO ASI 533MM Pro",
        telescope: str = "Apertura 75Q",
        exposure: float = 60.0,
        width_arcsec: float | None = 6.0,
        role: str = "LIGHT",
        filter_name: str = "Luminance",
    ) -> None:
        """Store the fields a capture frame reads."""
        _frame_counter["next"] += 1
        self.path = f"/lib/lights/Target/x/frame_{_frame_counter['next']}.fits"
        self.stored_width_arcsec = width_arcsec
        self.timestamp = 1_700_000_000.0
        self.sensor_temperature_c = None
        self.altitude_deg = None
        self.azimuth_deg = None
        self.pier_side = None
        self.binning = 1
        self.camera = camera
        self.telescope = telescope
        self.exposure_s = exposure
        self.pixel_scale_arcsec = 2.0
        self.role = role
        self.filter = filter_name
        self.is_spectral = frame_is_spectral({"filter": filter_name})


def _fake_astrometrics(frames: list[_Frame], config: AppConfiguration) -> FakeAstrometrics:
    """Build a science-library stand-in whose frames have stored star widths.

    Returns
    -------
    astrometrics : `FakeAstrometrics`
        Holds the frames and a stored star-width row for each with a width.
    """
    return FakeAstrometrics(
        frames,
        {frame.path: {STAR_WIDTH_METRIC: frame.stored_width_arcsec} for frame in frames},
        camera_config=config,
    )


def _measure(astrometrics: FakeAstrometrics) -> MeasuredImageQuality | None:
    """Measure star width for this observatory's rig with a 10 second cut.

    Returns
    -------
    quality : `MeasuredImageQuality` or `None`
        The measured star width, if any frame qualified.
    """
    return measure_image_quality(astrometrics, "Apertura 75Q", "ZWO ASI 533MM Pro", 10.0)


def test_star_width_is_the_median_of_the_stored_widths(
    camera_profile_config: AppConfiguration,
) -> None:
    """Verify the star width is the median of the stored arcsecond widths."""
    frames = [_Frame(width_arcsec=6.0), _Frame(width_arcsec=4.0), _Frame(width_arcsec=8.0)]

    quality = _measure(_fake_astrometrics(frames, camera_profile_config))

    assert quality.fwhm_arcsec == pytest.approx(6.0)
    assert quality.sample_count == 3
    assert quality.minimum_exposure_s == pytest.approx(10.0)


def test_short_exposures_are_left_out(camera_profile_config: AppConfiguration) -> None:
    """Verify frames too short for guiding to show in them do not count.

    Regression test, found on real data: bright single stars shot for 1 to
    4 seconds read 10 to 16 arcseconds wide (saturated), and pulled the
    median star width from 5.6 to 8.0 arcseconds, which loosened every
    guiding limit.
    """
    long_frames = [_Frame(width_arcsec=6.0, exposure=60.0) for _ in range(5)]
    short_saturated_frames = [_Frame(width_arcsec=16.0, exposure=2.0) for _ in range(20)]

    quality = measure_image_quality(
        _fake_astrometrics(long_frames + short_saturated_frames, camera_profile_config),
        "Apertura 75Q",
        "ZWO ASI 533MM Pro",
        10.0,
    )

    assert quality.fwhm_arcsec == pytest.approx(6.0)
    assert quality.sample_count == 5


def test_frames_of_other_equipment_are_left_out(camera_profile_config: AppConfiguration) -> None:
    """Verify only this telescope and camera's frames are measured."""
    frames = [
        _Frame(width_arcsec=6.0),
        _Frame(width_arcsec=40.0, camera="Nikon DSLR DSC D5300"),
        _Frame(width_arcsec=40.0, telescope="Nikkor 300mm"),
    ]

    quality = _measure(_fake_astrometrics(frames, camera_profile_config))

    assert quality.sample_count == 1


def test_a_cameras_other_spellings_are_accepted(camera_profile_config: AppConfiguration) -> None:
    """Verify the FITS-header spelling matches the library one."""
    frames = [_Frame(camera="ZWO CCD ASI533MM Pro", width_arcsec=6.0)]

    quality = _measure(_fake_astrometrics(frames, camera_profile_config))

    assert quality is not None
    assert quality.sample_count == 1


def test_spectroscopy_and_calibration_frames_are_left_out(camera_profile_config: AppConfiguration) -> None:
    """Verify dispersed stars and non-light frames are not measured."""
    frames = [
        _Frame(width_arcsec=6.0),
        _Frame(width_arcsec=60.0, filter_name="Star Analyzer 200"),
        _Frame(width_arcsec=60.0, role="DARK"),
    ]

    quality = _measure(_fake_astrometrics(frames, camera_profile_config))

    assert quality.sample_count == 1


def test_frames_with_no_measured_width_are_left_out(camera_profile_config: AppConfiguration) -> None:
    """Verify an unmeasured frame is skipped, not counted as zero."""
    frames = [_Frame(width_arcsec=6.0), _Frame(width_arcsec=None)]

    quality = _measure(_fake_astrometrics(frames, camera_profile_config))

    assert quality.sample_count == 1


def test_no_matching_frames_gives_none(camera_profile_config: AppConfiguration) -> None:
    """Verify equipment with no frames has no measured star width."""
    assert _measure(_fake_astrometrics([], camera_profile_config)) is None


def test_sensor_limits_come_from_the_camera_profile(camera_profile_config: AppConfiguration) -> None:
    """Verify saturation facts and provenance come from the profile."""
    limits = sensor_limits_for_camera(
        library_with_camera_profiles(camera_profile_config), "ZWO ASI 533MM Pro"
    )

    assert limits.clip_ceiling_adu == pytest.approx(65532.0)
    assert limits.clip_ceiling_source.startswith("measured:")
    assert limits.is_generic_fallback is False


def test_an_unlisted_camera_gets_the_generic_stand_in(camera_profile_config: AppConfiguration) -> None:
    """Verify a camera without a profile is marked as a stand-in."""
    assert (
        sensor_limits_for_camera(
            library_with_camera_profiles(camera_profile_config), "Some Unlisted Camera 9000"
        ).is_generic_fallback
        is True
    )


_NIGHT_START = 1790217108.0


def _session(night: str, started_at: float, fingerprint: str) -> EkosSessionContext:
    """Build a two-hour session record with the given equipment fingerprint.

    Returns
    -------
    context : `EkosSessionContext`
        The session record.
    """
    return EkosSessionContext(
        id=f"session-{night}-{fingerprint}",
        session_id=night,
        started_at=started_at,
        ended_at=started_at + 7200.0,
        equipment=SessionEquipmentAttribution(equipment_fingerprint=fingerprint),
    )


def _session_of(started_at: float, fingerprint: str = "fp-a") -> EkosSessionContext:
    """Build a session record for the night that `started_at` falls in.

    Returns
    -------
    context : `EkosSessionContext`
        The session record.
    """
    return _session(observing_night_id(started_at), started_at, fingerprint)


@pytest.fixture
def records(tmp_path: Path) -> ControlRecordStore:
    """Build a real `ControlRecordStore` on a temporary database.

    Returns
    -------
    records : `ControlRecordStore`
        An initialised interface, isolated from the real log database.
    """
    return ControlRecordStore(db_path=str(tmp_path / "log.db"))


def _record_night(records: ControlRecordStore, started_at: float, snr: float, error: float) -> None:
    """Record 200 measured guide samples, 3 seconds apart, for one night."""
    records.replace_guiding_samples([
        {
            "timestamp": started_at + 60.0 + 3.0 * index,
            "dra": error * (1 if index % 2 else -1),
            "ddec": error * (1 if index % 3 else -1),
            "snr": snr,
            "source": "ekos_guide_log",
        }
        for index in range(200)
    ])


def _sample(timestamp: float, source: str, error: float = 1.0, snr: float = 300.0) -> dict:
    """Build one guide sample.

    Returns
    -------
    sample : `dict`
        A sample ready for `ControlRecordStore.replace_guiding_samples`.
    """
    return {"timestamp": timestamp, "dra": error, "ddec": error, "snr": snr, "source": source}


def test_the_baseline_has_one_value_per_qualifying_night(records: ControlRecordStore) -> None:
    """Verify each night gives one SNR, one error and one cadence."""
    contexts = []
    for day, (snr, error) in enumerate([(300.0, 1.0), (280.0, 1.2), (320.0, 0.9)]):
        started_at = _NIGHT_START + day * 86400.0
        _record_night(records, started_at, snr, error)
        contexts.append(_session(observing_night_id(started_at), started_at, "fp-a"))

    baseline = collect_baseline_values(records, contexts, "fp-a")

    assert len(baseline["guide_snr"]) == 3
    assert sorted(baseline["guide_snr"]) == pytest.approx([280.0, 300.0, 320.0])
    assert baseline["guide_cadence_seconds"] == pytest.approx([3.0, 3.0, 3.0])


def test_sessions_of_other_equipment_are_not_read(records: ControlRecordStore) -> None:
    """Verify a change of equipment starts with an empty history.

    This is what lets the limits follow the equipment with no other step:
    the new fingerprint simply has no sessions yet.
    """
    started_at = _NIGHT_START
    _record_night(records, started_at, 300.0, 1.0)
    old_equipment_session = _session(observing_night_id(started_at), started_at, "old-fingerprint")

    baseline = collect_baseline_values(records, [old_equipment_session], "new-fingerprint")

    assert baseline == {
        "guide_snr": [],
        "guide_star_mass": [],
        "guiding_rms": [],
        "guide_cadence_seconds": [],
        "guide_lost_fraction": [],
    }


def test_a_session_without_attribution_is_not_used(records: ControlRecordStore) -> None:
    """Verify a session whose equipment is unknown joins no baseline."""
    _record_night(records, _NIGHT_START, 300.0, 1.0)
    unattributed = EkosSessionContext(
        id="x",
        session_id=observing_night_id(_NIGHT_START),
        started_at=_NIGHT_START,
        ended_at=_NIGHT_START + 7200.0,
    )

    assert collect_baseline_values(records, [unattributed], "fp-a")["guide_snr"] == []


def test_a_night_with_too_few_samples_is_skipped(records: ControlRecordStore) -> None:
    """Verify a short test run does not count as a session."""
    records.replace_guiding_samples([
        {
            "timestamp": _NIGHT_START + 60.0 + 3.0 * index,
            "dra": 1.0,
            "ddec": 1.0,
            "snr": 300.0,
            "source": "ekos_guide_log",
        }
        for index in range(MINIMUM_SAMPLES_PER_SESSION.default - 1)
    ])

    baseline = collect_baseline_values(
        records, [_session(observing_night_id(_NIGHT_START), _NIGHT_START, "fp-a")], "fp-a"
    )

    assert baseline["guide_snr"] == []


def test_estimated_samples_never_enter_the_baseline(records: ControlRecordStore) -> None:
    """Verify pulse-derived estimates are not read, however many there are."""
    records.replace_guiding_samples([
        {
            "timestamp": _NIGHT_START + 60.0 + 3.0 * index,
            "dra": 9.0,
            "ddec": 9.0,
            "snr": 22.0,
            "source": "indi_pulse_estimate",
        }
        for index in range(500)
    ])

    assert len(records.get_guiding_samples(limit=1000)) == 500  # they really are stored
    baseline = collect_baseline_values(
        records, [_session(observing_night_id(_NIGHT_START), _NIGHT_START, "fp-a")], "fp-a"
    )

    assert baseline["guide_snr"] == []


def _run(night_started_at: float, frames_total: int, frames_lost: int) -> GuidingRunSummary:
    """Build a guiding run for the night that `night_started_at` falls in.

    Returns
    -------
    run : `GuidingRunSummary`
        A run starting a minute into the session.
    """
    return GuidingRunSummary(
        id=f"run-{night_started_at}",
        session_id=observing_night_id(night_started_at),
        source_file_name="guide_log.txt",
        written_by_ekos=True,
        started_at=night_started_at + 60.0,
        frames_total=frames_total,
        frames_lost=frames_lost,
    )


def test_the_baseline_includes_each_nights_share_of_lost_frames(records: ControlRecordStore) -> None:
    """Verify the lost share comes from the run records, not the samples."""
    _record_night(records, _NIGHT_START, 300.0, 1.0)

    baseline = collect_baseline_values(
        records, [_session_of(_NIGHT_START)], "fp-a", [_run(_NIGHT_START, 200, 10)]
    )

    assert baseline["guide_lost_fraction"] == pytest.approx([0.05])


def test_lost_frames_of_other_nights_are_not_counted(records: ControlRecordStore) -> None:
    """Verify a run from another night does not change this night's share."""
    _record_night(records, _NIGHT_START, 300.0, 1.0)
    other_night = _run(_NIGHT_START + 10 * 86400.0, 100, 100)

    baseline = collect_baseline_values(
        records, [_session_of(_NIGHT_START)], "fp-a", [_run(_NIGHT_START, 200, 0), other_night]
    )

    assert baseline["guide_lost_fraction"] == pytest.approx([0.0])


def test_the_excursion_baseline_counts_samples_beyond_the_limit(records: ControlRecordStore) -> None:
    """Verify the share of samples whose total error exceeds the limit."""
    records.replace_guiding_samples([
        _sample(_NIGHT_START + 60.0 + 3.0 * index, "ekos_guide_log", error=10.0 if index < 20 else 0.5)
        for index in range(200)
    ])

    fractions = collect_excursion_fraction_baseline(
        records, [_session_of(_NIGHT_START)], "fp-a", excursion_limit_arcsec=6.0
    )

    assert fractions == pytest.approx([0.1])


def _live_samples(started_at: float, count: int, offset: int = 0) -> list[dict]:
    """Build live guide samples, 2 seconds apart, as guiding stores them.

    Returns
    -------
    samples : `list` [`dict`]
        Samples ready for `ControlRecordStore.record_guiding_samples`.
    """
    return [
        _sample(started_at + 100.0 + 2.0 * (offset + index), "phd2_live", error=0.5 + 0.01 * index)
        for index in range(count)
    ]


def _three_nights(
    records: ControlRecordStore,
) -> tuple[list[EkosSessionContext], list[GuidingRunSummary], float]:
    """Record two finished nights from a log sync and a live night tonight.

    Returns
    -------
    contexts, runs, tonight : `tuple`
        The session records, the guiding runs and the start of tonight.
    """
    contexts, runs = [], []
    for day in range(2):
        started_at = _NIGHT_START + day * 86400.0
        _record_night(records, started_at, 300.0 + day, 1.0 + 0.1 * day)
        contexts.append(_session_of(started_at))
        runs.append(_run(started_at, 200, day))
    tonight = _NIGHT_START + 5 * 86400.0
    contexts.append(_session_of(tonight))
    records.record_guiding_samples(_live_samples(tonight, 120))
    return contexts, runs, tonight


def _same_table(first: NightBaselineTable, second: NightBaselineTable) -> bool:
    """Say whether two tables hold the same nights and numbers.

    Returns
    -------
    same : `bool`
        True when the nights, inputs and every baseline value agree
        (including each night's guide errors).
    """
    if first.inputs != second.inputs or len(first.nights) != len(second.nights):
        return False
    for one, other in zip(first.nights, second.nights, strict=True):
        if one._replace(total_errors=None) != other._replace(total_errors=None):
            return False
        if list(one.total_errors) != list(other.total_errors):
            return False
    return True


def _count_night_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count which nights' baselines are worked out.

    Returns
    -------
    nights : `list` [`str`]
        Filled with each night worked out, in order.
    """
    worked_out: list[str] = []
    real = envelope_assembly.night_guiding_baseline

    def counted(records: Any, night: str, *arguments: Any) -> Any:
        """Note the night, then work it out.

        Returns
        -------
        baseline : `NightGuidingBaseline` or `None`
            As `night_guiding_baseline` gives it.
        """
        worked_out.append(night)
        return real(records, night, *arguments)

    monkeypatch.setattr(envelope_assembly, "night_guiding_baseline", counted)
    return worked_out


def test_the_table_lists_the_same_nights_as_collect_night_baselines(records: ControlRecordStore) -> None:
    """The table's qualifying nights are exactly what the baselines were."""
    contexts, runs, _ = _three_nights(records)

    table = read_night_baseline_table(records, contexts, "fp-a", runs)

    assert [night.night for night in table.nights] == [
        night.night for night in collect_night_baselines(records, contexts, "fp-a", runs)
    ]
    assert len(table.nights) == 3


def test_live_samples_work_out_only_tonight_again_and_equal_a_full_read(
    records: ControlRecordStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live samples change only tonight, and the result equals a full read."""
    contexts, runs, tonight = _three_nights(records)
    kept_newest = records.newest_guiding_sample_id()
    kept = read_night_baseline_table(records, contexts, "fp-a", runs)
    records.record_guiding_samples(_live_samples(tonight, 60, offset=120))
    worked_out = _count_night_reads(monkeypatch)

    changed = records.guiding_sample_nights_after(kept_newest)
    updated = update_night_baseline_table(records, contexts, "fp-a", runs, kept, changed)

    assert changed == {observing_night_id(tonight)}
    assert worked_out == [observing_night_id(tonight)]
    assert _same_table(updated, read_night_baseline_table(records, contexts, "fp-a", runs))
    assert updated.nights[-1].cadence_s == pytest.approx(2.0)
    baselines = {"guide_cadence_seconds": [night.cadence_s for night in updated.nights]}
    full = collect_baseline_values(records, contexts, "fp-a", runs)
    assert baselines["guide_cadence_seconds"] == pytest.approx(full["guide_cadence_seconds"])


def test_a_changed_session_or_run_works_out_only_that_night(
    records: ControlRecordStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session record or guiding run of one night changes only that night."""
    contexts, runs, _ = _three_nights(records)
    kept = read_night_baseline_table(records, contexts, "fp-a", runs)
    worked_out = _count_night_reads(monkeypatch)
    first_night = observing_night_id(_NIGHT_START)

    runs = [*runs, _run(_NIGHT_START + 600.0, 100, 50)]
    with_run = update_night_baseline_table(records, contexts, "fp-a", runs, kept, frozenset())
    assert worked_out == [first_night]
    assert _same_table(with_run, read_night_baseline_table(records, contexts, "fp-a", runs))

    worked_out.clear()
    second_start = _NIGHT_START + 86400.0
    contexts = [
        _session(observing_night_id(second_start), second_start, "fp-a").model_copy(
            update={"ended_at": second_start + 300.0}
        )
        if record.session_id == observing_night_id(second_start)
        else record
        for record in contexts
    ]
    with_session = update_night_baseline_table(records, contexts, "fp-a", runs, with_run, frozenset())
    assert worked_out == [observing_night_id(second_start)]
    assert _same_table(with_session, read_night_baseline_table(records, contexts, "fp-a", runs))


def test_a_night_whose_session_record_is_gone_is_dropped(records: ControlRecordStore) -> None:
    """A night with no session of this equipment left leaves the table."""
    contexts, runs, _ = _three_nights(records)
    kept = read_night_baseline_table(records, contexts, "fp-a", runs)

    updated = update_night_baseline_table(records, contexts[1:], "fp-a", runs, kept, frozenset())

    assert observing_night_id(_NIGHT_START) not in updated.by_night
    assert _same_table(updated, read_night_baseline_table(records, contexts[1:], "fp-a", runs))


def test_the_sample_nights_name_a_samples_session_and_night(records: ControlRecordStore) -> None:
    """A sample counts for its session id and for the night it was taken in."""
    newest = records.newest_guiding_sample_id()
    assert newest == 0
    records.record_guiding_samples([
        {**_sample(_NIGHT_START + 100.0, "phd2_live"), "session_id": "2026-01-01"}
    ])

    assert records.guiding_sample_nights_after(newest) == {"2026-01-01", observing_night_id(_NIGHT_START)}
    assert records.guiding_sample_nights_after(records.newest_guiding_sample_id()) == frozenset()
