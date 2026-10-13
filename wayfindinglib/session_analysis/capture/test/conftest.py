"""Purpose: Shared builders for the capture-analysis tests.

Description: Builds a realistic equipment envelope, light frames and Ekos
captures, so each test states only what it is about. The defaults match this
observatory: an ASI533 camera on a 75 mm telescope, a measured star width of
5.59 arcsec, and a guide cycle of about 3.2 seconds (so star measurements
count from 9.6 seconds of exposure).
"""

from typing import Any

import pytest

from wayfindinglib.analytics.performance_envelope import MeasuredImageQuality, derive_performance_envelope
from wayfindinglib.models.equipment_and_site.equipment import (
    Camera,
    EquipmentConfiguration,
    GuideScope,
    Telescope,
)
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.ekos_session import EkosCapture

NIGHT_START = 1790217108.0
"""2026-09-23 20:31:48 MDT, as seconds since the epoch."""

SENSOR_PIXELS = 3008 * 3008
"""Pixels on the ASI533 sensor."""

GOOD_CAPTURE_BASELINE = {
    "night_star_width": [5.2, 5.6, 5.4, 5.8, 5.5, 5.3],
    "night_star_roundness": [0.86, 0.88, 0.85, 0.87, 0.86, 0.89],
    "capture_abort_fraction": [0.0, 0.02, 0.01, 0.03, 0.0, 0.02],
}
"""Six earlier nights of this equipment, all ordinary."""


@pytest.fixture
def make_envelope() -> Any:
    """Return a function that builds this observatory's performance envelope.

    Returns
    -------
    make : `Callable`
        ``make(baseline=GOOD_CAPTURE_BASELINE)`` returns the envelope. Pass
        ``baseline=None`` for equipment with no earlier nights, and
        ``cadence=None`` for equipment that has not guided.
    """

    def make(baseline: dict | None = GOOD_CAPTURE_BASELINE, cadence: float | None = 3.2) -> Any:
        """Build the envelope.

        Returns
        -------
        envelope : `PerformanceEnvelope`
            The derived limits.
        """
        telescope = Telescope(id="t", name="Apertura 75Q", focal_length_mm=405.0)
        camera = Camera(
            id="c", name="ZWO ASI 533MM Pro", pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008
        )
        guide_camera = Camera(
            id="g", name="ZWO ASI120MC-S", pixel_size_um=3.75, sensor_width_px=1280, sensor_height_px=960
        )
        return derive_performance_envelope(
            EquipmentConfiguration(telescope=telescope, camera=camera),
            GuideScope(id="gs", name="Apertura 32mm", focal_length_mm=121.05, aperture_mm=32.0),
            guide_camera,
            "fingerprint-a",
            measured_image_quality=MeasuredImageQuality(5.59, 361),
            baseline_values=baseline,
            guide_cadence_seconds=cadence,
        )

    return make


@pytest.fixture
def make_frame() -> Any:
    """Return a function that builds one light frame.

    Returns
    -------
    make : `Callable`
        Builds a frame of one target; any field can be overridden.
    """

    def make(index: int = 0, **overrides: Any) -> CaptureFrame:
        """Build the frame.

        Returns
        -------
        frame : `CaptureFrame`
            A 30 second imaging frame `index` minutes into the night, with a
            clean star (no clipping), a 5.5 arcsec width and roundness 0.86.
        """
        fields: dict[str, Any] = {
            "path": f"/frames/Target_Light_{index:03d}.fits",
            "target_id": "Target",
            "timestamp": NIGHT_START + 60.0 * index,
            "exposure_seconds": 30.0,
            "filter_name": "Luminance",
            "is_spectral": False,
            "sensor_temperature_c": -10.0,
            "saturated_pixel_fraction": 0.0,
            "background_adu": 100.0,
            "star_width_arcsec": 5.5,
            "roundness": 0.86,
            "altitude_degrees": 50.0,
            "azimuth_degrees": 180.0,
            "pier_side": "East",
            "pixel_scale_arcsec": 2.0,
        }
        fields.update(overrides)
        return CaptureFrame(**fields)

    return make


@pytest.fixture
def make_capture() -> Any:
    """Return a function that builds one Ekos capture.

    Returns
    -------
    make : `Callable`
        Builds a capture that matches the frame `make_frame` builds for the
        same index.
    """

    def make(index: int = 0, **overrides: Any) -> EkosCapture:
        """Build the capture.

        Returns
        -------
        capture : `EkosCapture`
            A light exposure that ended 2 seconds after the matching frame's
            exposure did, as real Ekos logs do.
        """
        fields: dict[str, Any] = {
            "completed_at": NIGHT_START + 60.0 * index + 30.0 + 2.0,
            "exposure_seconds": 30.0,
            "filter_name": "Luminance",
            "file_path": f"/home/stellarmate/Pictures/Target/Light/Target_Light_{index:03d}.fits",
        }
        fields.update(overrides)
        return EkosCapture(**fields)

    return make
