"""Purpose: Unit tests for gathering performance-envelope inputs.

Description: Verifies how the envelope's inputs are read: the camera's
stored profile, the star width measured from the equipment's own frames
(including which frames are left out and why), and the per-session
baseline collected only from sessions of the same equipment. The baseline
tests use a real `LoggerInterface` on a temporary database.
"""

from pathlib import Path

import pytest

from astrometricslib import LoggerInterface, observing_night_id
from wayfindinglib.models.session.ekos_session import EkosSessionContext, SessionEquipmentAttribution
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.tasks.control_tasks.performance_envelope_tasks import (
    MINIMUM_SAMPLES_PER_SESSION,
    collect_baseline_values,
    collect_excursion_fraction_baseline,
    measure_image_quality,
    sensor_limits_for_camera,
)


@pytest.fixture
def camera_profile_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shipped_camera_sections: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Build a config holding the shipped camera profiles.

    Passed explicitly, so these tests do not depend on the process-wide
    configuration, which other test modules replace.

    Returns
    -------
    config : `AppConfiguration`
        A configuration with every shipped camera profile and the generic
        fallback.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config.toml"
    config_path.write_text(shipped_camera_sections, encoding="utf-8")
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


class _Measurements:
    """A minimal frame-measurements record."""

    def __init__(self, registration_fwhm_x_px: float | None) -> None:
        """Store the measurements a capture frame reads."""
        self.registration_fwhm_x_px = registration_fwhm_x_px
        self.saturated_pixel_fraction = None
        self.background_level = None
        self.registration_roundness = None


class _Frame:
    """A minimal light-frame record."""

    def __init__(
        self,
        camera: str = "ZWO ASI 533MM Pro",
        telescope: str = "Apertura 75Q",
        exposure: float = 60.0,
        fwhm_px: float | None = 3.0,
        pixel_scale: float = 2.0,
        role: str = "LIGHT",
        filter_name: str = "Luminance",
    ) -> None:
        """Store the fields a capture frame reads."""
        self.path = "frame.fits"
        self.timestamp = 1_700_000_000.0
        self.sensor_temperature_c = None
        self.altitude_degrees = None
        self.azimuth_degrees = None
        self.pier_side = None
        self.binning = 1
        self.camera = camera
        self.telescope = telescope
        self.exposure = exposure
        self.pixel_scale_arcsec = pixel_scale
        self.role = role
        self.filter = filter_name
        self.measurements = _Measurements(fwhm_px)


class _Astrometrics:
    """A minimal science-library interface holding one target of frames."""

    def __init__(self, frames: list[_Frame]) -> None:
        """Hold the frames behind a `targets.list()` call."""
        target = type("Target", (), {"frames": frames, "id": "Target"})()

        class _Targets:
            """Stand-in for the target catalog."""

            @staticmethod
            def list() -> list:
                """Return the one target.

                Returns
                -------
                targets : `list`
                    The single target.
                """
                return [target]

        self.targets = _Targets()


def _measure(astrometrics: _Astrometrics, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Measure star width for this observatory's rig with a 10 second cut.

    Returns
    -------
    quality : `MeasuredImageQuality` or `None`
        The measured star width, if any frame qualified.
    """
    return measure_image_quality(astrometrics, "Apertura 75Q", "ZWO ASI 533MM Pro", 10.0, config)


def test_star_width_is_the_median_converted_with_each_frames_own_plate_scale(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify width in pixels x plate scale, then the median."""
    frames = [_Frame(fwhm_px=3.0, pixel_scale=2.0), _Frame(fwhm_px=2.0, pixel_scale=2.0), _Frame(fwhm_px=4.0)]

    quality = _measure(_Astrometrics(frames), camera_profile_config)

    assert quality.fwhm_arcsec == pytest.approx(6.0)
    assert quality.sample_count == 3
    assert quality.minimum_exposure_seconds == pytest.approx(10.0, camera_profile_config)


def test_short_exposures_are_left_out(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify frames too short for guiding to show in them do not count.

    Regression test, found on real data: bright single stars shot for 1 to
    4 seconds read 10 to 16 arcseconds wide (saturated), and pulled the
    median star width from 5.6 to 8.0 arcseconds, which loosened every
    guiding limit.
    """
    long_frames = [_Frame(fwhm_px=3.0, exposure=60.0) for _ in range(5)]
    short_saturated_frames = [_Frame(fwhm_px=8.0, exposure=2.0) for _ in range(20)]

    quality = measure_image_quality(
        _Astrometrics(long_frames + short_saturated_frames),
        "Apertura 75Q",
        "ZWO ASI 533MM Pro",
        10.0,
        camera_profile_config,
    )

    assert quality.fwhm_arcsec == pytest.approx(6.0)
    assert quality.sample_count == 5


def test_frames_of_other_equipment_are_left_out(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify only this telescope and camera's frames are measured."""
    frames = [
        _Frame(fwhm_px=3.0),
        _Frame(fwhm_px=20.0, camera="Nikon DSLR DSC D5300"),
        _Frame(fwhm_px=20.0, telescope="Nikkor 300mm"),
    ]

    quality = _measure(_Astrometrics(frames), camera_profile_config)

    assert quality.sample_count == 1


def test_a_cameras_other_spellings_are_accepted(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify the FITS-header spelling matches the library one."""
    frames = [_Frame(camera="ZWO CCD ASI533MM Pro", fwhm_px=3.0)]

    quality = _measure(_Astrometrics(frames), camera_profile_config)

    assert quality is not None
    assert quality.sample_count == 1


def test_spectroscopy_and_calibration_frames_are_left_out(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify dispersed stars and non-light frames are not measured."""
    frames = [
        _Frame(fwhm_px=3.0),
        _Frame(fwhm_px=30.0, filter_name="Star Analyzer 200"),
        _Frame(fwhm_px=30.0, role="DARK"),
    ]

    quality = _measure(_Astrometrics(frames), camera_profile_config)

    assert quality.sample_count == 1


def test_frames_with_no_measured_width_are_left_out(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify an unmeasured frame is skipped, not counted as zero."""
    frames = [_Frame(fwhm_px=3.0), _Frame(fwhm_px=None)]

    quality = _measure(_Astrometrics(frames), camera_profile_config)

    assert quality.sample_count == 1


def test_no_matching_frames_gives_none(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify equipment with no frames has no measured star width."""
    assert _measure(_Astrometrics([]), camera_profile_config) is None


def test_sensor_limits_come_from_the_camera_profile(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify saturation facts and provenance come from the profile."""
    limits = sensor_limits_for_camera("ZWO ASI 533MM Pro", camera_profile_config)

    assert limits.clip_ceiling_adu == pytest.approx(65532.0)
    assert limits.clip_ceiling_source.startswith("measured:")
    assert limits.is_generic_fallback is False


def test_an_unlisted_camera_gets_the_generic_stand_in(camera_profile_config) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify a camera without a profile is marked as a stand-in."""
    assert (
        sensor_limits_for_camera("Some Unlisted Camera 9000", camera_profile_config).is_generic_fallback
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
def logger_interface(tmp_path: Path) -> LoggerInterface:
    """Build a real `LoggerInterface` on a temporary database.

    Returns
    -------
    logger_interface : `LoggerInterface`
        An initialised interface, isolated from the real log database.
    """
    return LoggerInterface(db_path=str(tmp_path / "log.db"))


def _record_night(logger_interface: LoggerInterface, started_at: float, snr: float, error: float) -> None:
    """Record 200 measured guide samples, 3 seconds apart, for one night."""
    logger_interface.replace_guiding_samples([
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
        A sample ready for `LoggerInterface.replace_guiding_samples`.
    """
    return {"timestamp": timestamp, "dra": error, "ddec": error, "snr": snr, "source": source}


def test_the_baseline_has_one_value_per_qualifying_night(logger_interface: LoggerInterface) -> None:
    """Verify each night gives one SNR, one error and one cadence."""
    contexts = []
    for day, (snr, error) in enumerate([(300.0, 1.0), (280.0, 1.2), (320.0, 0.9)]):
        started_at = _NIGHT_START + day * 86400.0
        _record_night(logger_interface, started_at, snr, error)
        contexts.append(_session(observing_night_id(started_at), started_at, "fp-a"))

    baseline = collect_baseline_values(logger_interface, contexts, "fp-a")

    assert len(baseline["guide_snr"]) == 3
    assert sorted(baseline["guide_snr"]) == pytest.approx([280.0, 300.0, 320.0])
    assert baseline["guide_cadence_seconds"] == pytest.approx([3.0, 3.0, 3.0])


def test_sessions_of_other_equipment_are_not_read(logger_interface: LoggerInterface) -> None:
    """Verify a change of equipment starts with an empty history.

    This is what lets the limits follow the equipment with no other step:
    the new fingerprint simply has no sessions yet.
    """
    started_at = _NIGHT_START
    _record_night(logger_interface, started_at, 300.0, 1.0)
    old_equipment_session = _session(observing_night_id(started_at), started_at, "old-fingerprint")

    baseline = collect_baseline_values(logger_interface, [old_equipment_session], "new-fingerprint")

    assert baseline == {
        "guide_snr": [],
        "guide_star_mass": [],
        "guiding_rms": [],
        "guide_cadence_seconds": [],
        "guide_lost_fraction": [],
    }


def test_a_session_without_attribution_is_not_used(logger_interface: LoggerInterface) -> None:
    """Verify a session whose equipment is unknown joins no baseline."""
    _record_night(logger_interface, _NIGHT_START, 300.0, 1.0)
    unattributed = EkosSessionContext(
        id="x",
        session_id=observing_night_id(_NIGHT_START),
        started_at=_NIGHT_START,
        ended_at=_NIGHT_START + 7200.0,
    )

    assert collect_baseline_values(logger_interface, [unattributed], "fp-a")["guide_snr"] == []


def test_a_night_with_too_few_samples_is_skipped(logger_interface: LoggerInterface) -> None:
    """Verify a short test run does not count as a session."""
    logger_interface.replace_guiding_samples([
        {
            "timestamp": _NIGHT_START + 60.0 + 3.0 * index,
            "dra": 1.0,
            "ddec": 1.0,
            "snr": 300.0,
            "source": "ekos_guide_log",
        }
        for index in range(MINIMUM_SAMPLES_PER_SESSION - 1)
    ])

    baseline = collect_baseline_values(
        logger_interface, [_session(observing_night_id(_NIGHT_START), _NIGHT_START, "fp-a")], "fp-a"
    )

    assert baseline["guide_snr"] == []


def test_estimated_samples_never_enter_the_baseline(logger_interface: LoggerInterface) -> None:
    """Verify pulse-derived estimates are not read, however many there are."""
    logger_interface.replace_guiding_samples([
        {
            "timestamp": _NIGHT_START + 60.0 + 3.0 * index,
            "dra": 9.0,
            "ddec": 9.0,
            "snr": 22.0,
            "source": "indi_pulse_estimate",
        }
        for index in range(500)
    ])

    assert len(logger_interface.get_guiding_logs(limit=1000)) == 500  # they really are stored
    baseline = collect_baseline_values(
        logger_interface, [_session(observing_night_id(_NIGHT_START), _NIGHT_START, "fp-a")], "fp-a"
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


def test_the_baseline_includes_each_nights_share_of_lost_frames(logger_interface: LoggerInterface) -> None:
    """Verify the lost share comes from the run records, not the samples."""
    _record_night(logger_interface, _NIGHT_START, 300.0, 1.0)

    baseline = collect_baseline_values(
        logger_interface, [_session_of(_NIGHT_START)], "fp-a", [_run(_NIGHT_START, 200, 10)]
    )

    assert baseline["guide_lost_fraction"] == pytest.approx([0.05])


def test_lost_frames_of_other_nights_are_not_counted(logger_interface: LoggerInterface) -> None:
    """Verify a run from another night does not change this night's share."""
    _record_night(logger_interface, _NIGHT_START, 300.0, 1.0)
    other_night = _run(_NIGHT_START + 10 * 86400.0, 100, 100)

    baseline = collect_baseline_values(
        logger_interface, [_session_of(_NIGHT_START)], "fp-a", [_run(_NIGHT_START, 200, 0), other_night]
    )

    assert baseline["guide_lost_fraction"] == pytest.approx([0.0])


def test_the_excursion_baseline_counts_samples_beyond_the_limit(logger_interface: LoggerInterface) -> None:
    """Verify the share of samples whose total error exceeds the limit."""
    logger_interface.replace_guiding_samples([
        _sample(_NIGHT_START + 60.0 + 3.0 * index, "ekos_guide_log", error=10.0 if index < 20 else 0.5)
        for index in range(200)
    ])

    fractions = collect_excursion_fraction_baseline(
        logger_interface, [_session_of(_NIGHT_START)], "fp-a", excursion_limit_arcsec=6.0
    )

    assert fractions == pytest.approx([0.1])
