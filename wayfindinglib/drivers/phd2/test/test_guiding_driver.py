"""Purpose: Test that PHD2 is the authority for guiding while it is reachable.

Description: `Phd2GuidingDriver` hands on PHD2's guide steps. While PHD2 is
reachable, the mount's guide pulses (which include PHD2's calibration
pulses) must not become guide samples, but they are still drained. When PHD2
is not reachable, the pulses are read as before. No PHD2 is started; the
service and the fallback are fakes.
"""

import asyncio
from unittest.mock import MagicMock

from wayfindinglib.drivers.indi.guiding_driver import IndiGuidingDriver
from wayfindinglib.drivers.phd2.guiding_driver import Phd2GuidingDriver
from wayfindinglib.models.session.telemetry import GuidingSample, GuidingSampleSource

_CALIBRATION_PULSES = [{"time": 1.0 + index, "pulse_w": 750.0} for index in range(3)]


def _driver(reachable: bool, steps: list[GuidingSample]) -> tuple[Phd2GuidingDriver, MagicMock]:
    """Build a driver over a fake PHD2 service and a fake pulse source.

    Parameters
    ----------
    reachable : `bool`
        Whether the fake PHD2 connection is open.
    steps : `list` [`GuidingSample`]
        The guide steps the fake PHD2 has stored.

    Returns
    -------
    driver : `Phd2GuidingDriver`
        The driver under test.
    session : `~unittest.mock.MagicMock`
        The fake INDI session, to check that its pulses were drained.
    """
    service = MagicMock()
    service.is_reachable.return_value = reachable
    service.drain_guiding_samples.return_value = steps
    session = MagicMock()
    session.drain_external_pulses.return_value = list(_CALIBRATION_PULSES)
    return Phd2GuidingDriver(service, IndiGuidingDriver(session)), session


def test_calibration_pulses_are_not_guiding_while_phd2_is_reachable() -> None:
    """A calibrating PHD2 sends pulses and no steps: no samples."""
    driver, session = _driver(reachable=True, steps=[])

    reading = asyncio.run(driver.read_samples())

    assert reading.samples == []
    assert reading.source is None
    session.drain_external_pulses.assert_called_once()


def test_the_pulse_fallback_works_when_phd2_is_not_reachable() -> None:
    """With no PHD2 (Ekos guides itself), the pulses still become samples."""
    driver, _ = _driver(reachable=False, steps=[])

    reading = asyncio.run(driver.read_samples())

    assert reading.samples
    assert reading.source is GuidingSampleSource.INDI_PULSE_ESTIMATE


def test_phd2_guide_steps_are_unchanged() -> None:
    """GuideStep samples come back as PHD2_LIVE; pulses are still drained."""
    step = GuidingSample(time=1.0, dra=0.2, ddec=-0.1, pulse_ra=40.0, pulse_dec=0.0)
    driver, session = _driver(reachable=True, steps=[step])

    reading = asyncio.run(driver.read_samples())

    assert reading.samples == [step]
    assert reading.source is GuidingSampleSource.PHD2_LIVE
    session.drain_external_pulses.assert_called_once()
