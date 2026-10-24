"""Purpose: End-to-end test of the Foundation performance-envelope assembly.

Description: Runs `assemble_performance_envelope` (the function Planning and
the Control analyses share) against the isolated configuration, a real
Butler and log database, and a stand-in science library whose frames have
stored star-width measurements. It checks that the star width in the
limits comes from the stored measurements, and that the envelope is the same
whether it is built directly or through the Control entry point.
"""

import pytest

from wayfindinglib import ObservatoryControl
from wayfindinglib.analytics import capture_history
from wayfindinglib.analytics.capture_history import STAR_WIDTH_METRIC, normalize_name
from wayfindinglib.analytics.envelope_assembly import assemble_performance_envelope
from wayfindinglib.analytics.test.frame_measurement_helpers import FakeAstrometrics
from wayfindinglib.api.test.guiding_night_helpers import FIRST_NIGHT, record_good_history
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.tasks.control_tasks import night_analysis


@pytest.fixture(autouse=True)
def _spell_cameras_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the camera-profile lookup the isolated configuration lacks."""
    monkeypatch.setattr(
        capture_history, "equipment_spellings", lambda name, config=None: {normalize_name(name)}
    )


class _LightFrame:
    """A minimal light-frame record of the active equipment."""

    def __init__(self, index: int, telescope: str, camera: str) -> None:
        """Fill the fields a capture frame reads."""
        self.path = f"/lib/lights/Target/x/frame_{index}.fits"
        self.role = "LIGHT"
        self.timestamp = FIRST_NIGHT + 60.0 * index
        self.exposure = 60.0
        self.filter = "Luminance"
        self.camera = camera
        self.telescope = telescope
        self.sensor_temperature_c = -10.0
        self.altitude_degrees = 50.0
        self.azimuth_degrees = 180.0
        self.pier_side = "East"
        self.pixel_scale_arcsec = 2.0
        self.binning = 1


def test_the_star_width_in_the_limits_comes_from_the_stored_measurements(control: ObservatoryControl) -> None:
    """Verify the guiding limit is built from the stored Moffat star widths."""
    record_good_history(control)
    catalog = get_equipment_catalog(control._context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    frames = [_LightFrame(index, telescope.name, camera.name) for index in range(30)]
    astrometrics = FakeAstrometrics(frames, {frame.path: {STAR_WIDTH_METRIC: 5.0} for frame in frames})

    envelope = assemble_performance_envelope(
        config=control._context.config,
        butler=control._context.butler,
        astrometrics=astrometrics,
        records=control._context.records,
    )

    assert envelope is not None
    assert envelope.limit_inputs["guiding_rms_limit"]["fwhm_arcsec"] == pytest.approx(5.0)
    assert envelope.thresholds["guiding_rms_limit"].sample_count == 30
    assert envelope.thresholds["guiding_rms_limit"].register_entry == "WAY-41"


def test_the_control_entry_point_gives_the_same_envelope(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the Control task is a thin caller of the Foundation function."""
    record_good_history(control)
    catalog = get_equipment_catalog(control._context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    frames = [_LightFrame(index, telescope.name, camera.name) for index in range(30)]
    astrometrics = FakeAstrometrics(frames, {frame.path: {STAR_WIDTH_METRIC: 5.0} for frame in frames})
    monkeypatch.setattr(control._context, "_astrometrics", astrometrics)

    through_control = night_analysis.performance_envelope(
        control._context, None, None, *night_analysis.recorded_sessions(control._context), {}
    )
    direct = assemble_performance_envelope(
        config=control._context.config,
        butler=control._context.butler,
        astrometrics=astrometrics,
        records=control._context.records,
    )

    assert through_control == direct
