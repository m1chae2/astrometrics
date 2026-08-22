"""Purpose: Unit tests for the relocated ObservationSessionRecorder.

Description: Verifies capture_weather_snapshot's INDI-status parsing,
save_checkpoint's round-trip through DiskButler onto the new
`ObservationSession` model, run()'s requirement that the session
already exist (Planning's field-ownership boundary), and that drained
`models.telemetry.GuidingSample` records are accumulated onto the
session unchanged.
"""

import threading
from datetime import date

import pytest

from astrometricslib import AppConfiguration
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.session.observation_session import ObservationSession, SessionStatus
from wayfindinglib.models.session.telemetry import GuidingSample
from wayfindinglib.tasks.execution_tasks.session_recorder import ObservationSessionRecorder


class _FakeTelescopeStatus:
    """A minimal stand-in for TelescopeStatus, carrying only weather fields."""

    def __init__(self, temperature, humidity):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.temperature = temperature
        self.humidity = humidity


class FakeIndiDriver:
    """A fake INDI driver with a fixed status, for weather-snapshot tests."""

    def __init__(self, temperature="12.3°C", humidity="45.0%"):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._status = _FakeTelescopeStatus(temperature, humidity)

    def get_status(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the fixed fake TelescopeStatus.

        Returns
        -------
        status : `Any`
            The fixed fake telescope status.
        """
        return self._status


class FakeGuidingService:
    """A fake guiding service returning a fixed sample list once."""

    def __init__(self, samples):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._samples = samples
        self.poll_count = 0

    def poll_external_telemetry(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Record that a poll occurred."""
        self.poll_count += 1

    def drain_guiding_samples(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return and clear the configured fake GuidingSample list.

        Returns
        -------
        samples : `list`
            The previously configured samples (now cleared).
        """
        drained, self._samples = self._samples, []
        return drained


@pytest.fixture
def butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by an isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh, isolated butler instance.
    """
    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _planned_session(session_id: str) -> ObservationSession:
    return ObservationSession(
        id=session_id,
        night_date=date(2026, 8, 10),
        status=SessionStatus.RUNNING,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id="cam-1",
    )


def test_capture_weather_snapshot_parses_available_indi_properties(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify capture_weather_snapshot parses temperature/humidity."""
    recorder = ObservationSessionRecorder(FakeGuidingService([]), FakeIndiDriver(), butler=butler)
    snapshot = recorder.capture_weather_snapshot()
    assert snapshot.ambient_temperature_c == pytest.approx(12.3)
    assert snapshot.humidity_percent == pytest.approx(45.0)


def test_capture_weather_snapshot_tolerates_unavailable_readings(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify unavailable INDI readings stay None rather than raising."""
    recorder = ObservationSessionRecorder(
        FakeGuidingService([]), FakeIndiDriver(temperature="-", humidity="Unknown"), butler=butler
    )
    snapshot = recorder.capture_weather_snapshot()
    assert snapshot.ambient_temperature_c is None
    assert snapshot.humidity_percent is None


def test_save_checkpoint_persists_session_via_butler(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify save_checkpoint round-trips through DiskButler."""
    recorder = ObservationSessionRecorder(FakeGuidingService([]), FakeIndiDriver(), butler=butler)
    session = _planned_session("test-checkpoint")
    recorder.save_checkpoint(session)

    reloaded = butler.get("observation_session", {"session_id": "test-checkpoint"})
    assert reloaded is not None
    assert reloaded.id == "test-checkpoint"


def test_run_raises_for_a_session_that_does_not_exist(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify run() raises rather than fabricating a never-planned session."""
    recorder = ObservationSessionRecorder(FakeGuidingService([]), FakeIndiDriver(), butler=butler)
    with pytest.raises(ValueError, match="not found"):
        recorder.run("does-not-exist")


def test_run_accumulates_guiding_samples_and_weather_until_stopped(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify one loop drains, snapshots weather, and checkpoints."""
    session = _planned_session("test-run-session")
    butler.put(session, "observation_session", {"session_id": session.id})

    samples = [GuidingSample(time=1000.0, dra=0.1, ddec=0.1, pulse_ra=10.0, pulse_dec=10.0, snr=25.0)]
    guiding_service = FakeGuidingService(samples)
    recorder = ObservationSessionRecorder(
        guiding_service, FakeIndiDriver(), butler=butler, snapshot_interval_seconds=0
    )

    stop_event = threading.Event()

    # run() polls once before the loop starts, then once per iteration --
    # stopping on the second poll call (the first in-loop one) keeps
    # this test to exactly one iteration's worth of accumulation.
    original_poll = guiding_service.poll_external_telemetry

    def poll_then_maybe_stop():  # ruff: ignore[missing-return-type-private-function]
        original_poll()
        if guiding_service.poll_count >= 2:
            stop_event.set()

    guiding_service.poll_external_telemetry = poll_then_maybe_stop

    result = recorder.run("test-run-session", stop_event=stop_event)

    assert len(result.guiding_samples) == 1
    recorded = result.guiding_samples[0]
    assert recorded.time == pytest.approx(1000.0)
    assert recorded.pulse_ra == pytest.approx(10.0)
    assert recorded.snr == pytest.approx(25.0)
    assert len(result.weather_samples) == 1

    reloaded = butler.get("observation_session", {"session_id": "test-run-session"})
    assert reloaded is not None
    assert len(reloaded.guiding_samples) == 1
