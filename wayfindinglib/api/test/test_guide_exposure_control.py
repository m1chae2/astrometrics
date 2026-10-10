"""Purpose: Tests for the guide exposure test on the facade.

Description: Runs `control.guiding.run_exposure_test` with a simulated
guide camera in place of the real one. It checks that the plate scale and
limits come from the configured equipment, that a good star gives a short
recommended exposure, and that an unreachable telescope is refused before any
exposure is sent. The real guide camera is not used.
"""

import numpy as np
import pytest

from astrometricslib import ExternalServiceError
from wayfindinglib import ObservatoryControl
from wayfindinglib.analytics.performance_envelope import SensorLimits
from wayfindinglib.api.test.capture_night_helpers import Library, add_frames
from wayfindinglib.api.test.guiding_night_helpers import record_guiding_night
from wayfindinglib.tasks.control_tasks import hardware_operations, remote_transfer_tasks

_SIZE = 64
"""Width and height of the simulated guide frame, in pixels."""


class _SimulatedCamera:
    """A guide camera showing one star, brighter at longer exposures."""

    def __init__(self, flux_per_second: float) -> None:
        """Start with an empty frame."""
        self.flux_per_second = flux_per_second
        self.exposures: list[float] = []
        self._frame = np.zeros((_SIZE, _SIZE))
        self._generator = np.random.default_rng(0)

    def expose(self, seconds: float, gain: float | None = None) -> bool:
        """Take an exposure and keep the frame it makes.

        Returns
        -------
        sent : `bool`
            Always `True`.
        """
        self.exposures.append(seconds)
        rows, columns = np.mgrid[:_SIZE, :_SIZE]
        index = len(self.exposures)
        centre = (
            32.0 + 0.03 * index + self._generator.normal(0, 0.03),
            30.0 + self._generator.normal(0, 0.03),
        )
        profile = np.exp(-((columns - centre[0]) ** 2 + (rows - centre[1]) ** 2) / (2.0 * 0.7**2))
        profile /= profile.sum()
        signal = self.flux_per_second * seconds * profile
        self._frame = 100.0 + signal + self._generator.normal(0.0, 4.0, signal.shape)
        return True

    def image(self) -> np.ndarray:
        """Return the latest frame.

        Returns
        -------
        frame : `numpy.ndarray`
            The latest frame.
        """
        return self._frame


@pytest.fixture
def camera(control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch) -> _SimulatedCamera:
    """Replace the real guide camera and its checks with a simulation.

    Returns
    -------
    camera : `_SimulatedCamera`
        The simulated guide camera, with a bright star.
    """
    simulated = _SimulatedCamera(flux_per_second=60000.0)
    monkeypatch.setattr(remote_transfer_tasks, "check_remote_connection", lambda context: True)
    monkeypatch.setattr(
        hardware_operations,
        "guide_expose",
        lambda context, seconds, gain=None: simulated.expose(seconds, gain),
    )
    monkeypatch.setattr(hardware_operations, "guide_image", lambda context: simulated.image())
    monkeypatch.setattr(
        "wayfindinglib.tasks.control_tasks.performance_envelope_tasks.sensor_limits_for_camera",
        lambda name, config=None: SensorLimits("Guider", 65532.0, "test", 65000.0, "test", False),
    )
    monkeypatch.setattr(
        "wayfindinglib.tasks.control_tasks.guide_exposure_ladder_tasks.time.sleep", lambda _: None
    )
    return simulated


def test_a_bright_star_recommends_the_shortest_exposure(
    control: ObservatoryControl, camera: _SimulatedCamera, library: Library
) -> None:
    """Verify the plate scale and a short exposure come from the equipment."""
    for day in range(6):
        record_guiding_night(control, day)
        add_frames(library, day)

    test = control.guiding.run_exposure_test((0.5, 1.0), frames_per_exposure=6)

    assert test.plate_scale_arcsec_per_px == pytest.approx(206.265 * 3.75 / 120.0)
    assert test.guiding_rms_limit_arcsec is not None
    assert test.recommended_exposure_seconds == pytest.approx(0.5)
    assert len(camera.exposures) == 12


def test_an_unreachable_telescope_is_refused_before_any_exposure(
    control: ObservatoryControl, camera: _SimulatedCamera, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify nothing is sent to the camera when the host is unreachable."""
    monkeypatch.setattr(remote_transfer_tasks, "check_remote_connection", lambda context: False)

    with pytest.raises(ExternalServiceError, match="not reachable"):
        control.guiding.run_exposure_test()

    assert camera.exposures == []
