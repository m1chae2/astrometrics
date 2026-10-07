"""Purpose: Tests for live guiding: the drivers, the RMS, and the guide loop.

Description: Covers the pieces that replaced the backend's guiding
service:

- the ``internal`` driver merges the right ascension and declination
  pulses of one correction and turns pulse lengths into sky motion at the
  guide rate, adding nothing;
- the ``phd2`` driver prefers PHD2's guide steps and falls back on the
  mount's pulses;
- `poll` adds the samples to the monitor, computes the RMS error, and
  records them labelled with their source (pulse estimates are never
  stored as measurements);
- `run_loop` with the ``simulator`` driver sends its pulses through the
  delegation-checked pulse command, stops when asked or when tracking is
  lost, and does not start when the mount does not track;
- the driver registry offers the three protocols.
"""

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from astrometricslib import ConfigurationError, ConflictError
from wayfindinglib.drivers.indi.guiding_driver import IndiGuidingDriver, samples_from_pulses
from wayfindinglib.drivers.phd2.guiding_driver import Phd2GuidingDriver
from wayfindinglib.drivers.protocols.guiding_driver import GuideCommands
from wayfindinglib.drivers.protocols.registry import build_guiding_driver_registry
from wayfindinglib.drivers.simulators.guiding_simulator import SimulatedGuidingDriver
from wayfindinglib.models.session.telemetry import GuidingSample
from wayfindinglib.tasks.control_tasks import hardware_operations, live_guiding


def _session(pulses: list[dict[str, float]]) -> MagicMock:
    """Build an INDI session stand-in that reports some guide pulses.

    Returns
    -------
    session : `unittest.mock.MagicMock`
        Its `drain_external_pulses` returns `pulses` once, then nothing.
    """
    session = MagicMock()
    session.drain_external_pulses.side_effect = [pulses, []]
    return session


def _context(driver: Any) -> SimpleNamespace:
    """Build the parts of a `ControlContext` live guiding reads.

    Returns
    -------
    context : `types.SimpleNamespace`
        The driver, a fresh monitor and a recording log database.
    """
    return SimpleNamespace(
        guiding_driver=driver, live_guiding=live_guiding.LiveGuidingMonitor(), logger_interface=MagicMock()
    )


@pytest.fixture
def mount(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the mount reads and the guide commands with recorders.

    Returns
    -------
    mount : `dict` [`str`, `Any`]
        ``tracking`` (the states to report, last one repeats), ``pulses``
        and ``exposures`` sent.
    """
    state: dict[str, Any] = {"tracking": ["Tracking"], "pulses": [], "exposures": []}

    def mount_status(context: Any, include: list[str]) -> dict[str, Any]:
        """Report the next tracking state and a target name.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            ``trackingStatus`` and ``targetName``.
        """
        tracking = state["tracking"].pop(0) if len(state["tracking"]) > 1 else state["tracking"][0]
        return {"trackingStatus": tracking, "targetName": "M31"}

    monkeypatch.setattr(hardware_operations, "mount_status", mount_status)
    monkeypatch.setattr(hardware_operations, "connect", lambda context: True)
    monkeypatch.setattr(
        hardware_operations,
        "pulse",
        lambda context, direction, ms: state["pulses"].append((direction, ms)) or True,
    )
    monkeypatch.setattr(
        hardware_operations,
        "guide_expose",
        lambda context, seconds, gain: state["exposures"].append((seconds, gain)) or True,
    )
    return state


def test_pulse_estimates_are_the_pulse_length_times_the_guide_rate() -> None:
    """250 ms west and 100 ms north become exactly 1.88 and 0.752 arcsec."""
    (sample,) = samples_from_pulses([{"time": 1.0, "pulse_w": 250.0, "pulse_n": 100.0}])
    assert sample.dra == pytest.approx(1.88)
    assert sample.ddec == pytest.approx(0.752)
    assert sample.snr is None


def test_an_axis_with_no_pulse_has_zero_estimate() -> None:
    """No drift is invented for an axis that received no pulse."""
    (sample,) = samples_from_pulses([{"time": 1.0, "pulse_w": 250.0}])
    assert sample.ddec == pytest.approx(0.0)


def test_close_pulses_merge_and_estimates_repeat() -> None:
    """Pulses 0.3 s apart are one correction; east and south are negative."""
    pulses = [{"time": 1.0, "pulse_e": 400.0}, {"time": 1.3, "pulse_s": 120.0}]
    first = samples_from_pulses(pulses)
    assert first == samples_from_pulses(pulses)
    (sample,) = first
    assert sample.dra == pytest.approx(-3.008)
    assert sample.ddec == pytest.approx(-0.902)


def test_poll_records_pulse_estimates_with_their_source(mount: dict) -> None:
    """Pulse samples are shown, given an RMS, and stored as estimates."""
    context = _context(IndiGuidingDriver(_session([{"time": 1.0, "pulse_w": 250.0}])))

    live_guiding.poll(context)

    status = context.live_guiding.status(now=2.0)
    assert status.is_guiding is True
    assert status.stats.rms_ra == pytest.approx(1.88)
    (record,) = context.logger_interface.record_guiding_samples.call_args.args[0]
    assert record["source"] == "indi_pulse_estimate"
    assert record["target_name"] == "M31"
    assert record["dra"] == pytest.approx(1.88)


def test_phd2_steps_come_first_and_keep_their_own_rms(mount: dict) -> None:
    """PHD2's steps are used with PHD2's own RMS and stored as PHD2 data."""
    phd2 = MagicMock()
    phd2.drain_guiding_samples.return_value = [
        GuidingSample(time=1.0, dra=0.25, ddec=-0.15, pulse_ra=45.0, pulse_dec=30.0, snr=28.5, rms_ra=0.18)
    ]
    context = _context(Phd2GuidingDriver(phd2, fallback=IndiGuidingDriver(_session([]))))

    live_guiding.poll(context)

    status = context.live_guiding.status(now=2.0)
    assert status.stats.rms_ra == pytest.approx(0.18)
    assert status.stats.rms_dec == pytest.approx(0.15)
    assert status.stats.snr == pytest.approx(28.5)
    (record,) = context.logger_interface.record_guiding_samples.call_args.args[0]
    assert record["source"] == "phd2_live"


def test_phd2_falls_back_on_the_mounts_pulses(mount: dict) -> None:
    """With nothing from PHD2, the mount's pulses are read instead."""
    phd2 = MagicMock()
    phd2.drain_guiding_samples.return_value = []
    context = _context(
        Phd2GuidingDriver(phd2, fallback=IndiGuidingDriver(_session([{"time": 1.0, "pulse_n": 100.0}])))
    )

    live_guiding.poll(context)

    assert len(context.live_guiding.status().history) == 1


def test_an_idle_guider_adds_nothing(mount: dict) -> None:
    """No pulses and no PHD2 steps give no samples and no record."""
    context = _context(IndiGuidingDriver(_session([])))

    live_guiding.poll(context)

    assert context.live_guiding.status().history == []
    assert context.live_guiding.status().is_guiding is False
    context.logger_interface.record_guiding_samples.assert_not_called()


def test_the_simulator_loop_guides_until_stopped(mount: dict) -> None:
    """The simulated loop exposes, sends gated pulses, and is not recorded."""
    driver = SimulatedGuidingDriver(IndiGuidingDriver(_session([])), random_source=np.random.default_rng(4))
    context = _context(driver)
    stop = threading.Event()
    cycles = {"count": 0}

    def sleep(seconds: float) -> None:
        """Count exposures; stop after five.

        Parameters
        ----------
        seconds : `float`
            Ignored.
        """
        cycles["count"] += 1
        if cycles["count"] >= 5:
            stop.set()

    live_guiding.run_loop(context, stop, exposure_seconds=2.0, gain=10.0, sleep=sleep)

    status = context.live_guiding.status()
    assert len(status.history) == 5
    assert mount["exposures"] == [(2.0, 10.0)] * 5
    assert all(direction in ("north", "south", "east", "west") for direction, _ in mount["pulses"])
    assert status.exposure == pytest.approx(2.0)
    assert status.stats.rms_total > 0
    context.logger_interface.record_guiding_samples.assert_not_called()


def test_the_loop_does_not_start_without_tracking(mount: dict) -> None:
    """A mount that never tracks refuses the loop with `ConflictError`."""
    mount["tracking"] = ["Idle"]
    context = _context(SimulatedGuidingDriver(IndiGuidingDriver(_session([]))))

    with pytest.raises(ConflictError, match="not tracking"):
        live_guiding.run_loop(context, threading.Event(), sleep=lambda seconds: None)
    assert context.live_guiding.running is False


def test_the_loop_stops_when_tracking_is_lost(mount: dict) -> None:
    """Three cycles in a row without tracking end the loop."""
    mount["tracking"] = ["Tracking", "Idle", "Idle", "Idle"]
    context = _context(SimulatedGuidingDriver(IndiGuidingDriver(_session([]))))

    live_guiding.run_loop(context, threading.Event(), sleep=lambda seconds: None)

    assert context.live_guiding.running is False
    assert mount["exposures"] == []


def test_phd2_and_internal_refuse_to_run_the_loop() -> None:
    """PHD2 runs its own loop; the internal guider cannot measure the star."""
    commands = GuideCommands(expose=lambda s, g: True, pulse=lambda d, ms: True, sleep=lambda s: None)
    with pytest.raises(ConflictError):
        asyncio.run(
            Phd2GuidingDriver(MagicMock(), IndiGuidingDriver(MagicMock())).run_cycle(1.0, None, commands)
        )
    with pytest.raises(ConfigurationError):
        asyncio.run(IndiGuidingDriver(MagicMock()).run_cycle(1.0, None, commands))


def test_the_registry_offers_the_three_protocols() -> None:
    """The guiding protocol setting can name phd2, internal or simulator."""
    assert set(build_guiding_driver_registry()) == {"phd2", "internal", "simulator"}
