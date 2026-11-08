"""Purpose: Live test of the fitted settings against the INDI simulators (S3).

Description: Starts the stand-ins of a simulated world (the INDI server with
the simulators, the stand-in guider and the exposure driver) in a temporary
folder, with a results file made from synthetic measurements. Then it checks,
with the INDI command line tools, that every property the file says to write
has that value after start, and that a few short frames drawn by the camera
simulator measure what the fit aimed at: the sky rate from the prediction and
the star width from the seeing. The numbers are measured with the library's
own unstored folder measurement (`quality.measure_folder`), with the
simulator's bias level as the pedestal.

Nothing real is touched: the library is a temporary one, the INDI server has
its own socket and home, and there is no PHD2. The test is skipped when the
INDI programs are missing, and when ``ASTROMETRICS_FAST_TEST`` is ``1``.

Run with ``.venv/bin/pytest simulator/test/test_fit_live.py -q -s`` (about
two minutes).
"""

import os
import shutil
import subprocess
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from simulator import observatory
from simulator.device_settings import matches
from simulator.fit.fitting import fit_values
from simulator.fit.measurements import Candidate, Measurements
from simulator.fit.results import RESULTS_FILE_NAME, fingerprint_library_copy, read_results, write_results
from simulator.fit.world_setup import world_setup_from_settings
from simulator.observatory import GuiderKind, SimulatedObservatory, SimulationKind
from simulator.settings import simulator_config_text

INDI_PROGRAMS = (
    "indiserver",
    "indi_setprop",
    "indi_getprop",
    *observatory.INDI_DRIVERS,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ASTROMETRICS_FAST_TEST") == "1"
    or any(shutil.which(name) is None for name in INDI_PROGRAMS),
    reason="The INDI simulators are missing, or ASTROMETRICS_FAST_TEST is set.",
)

#: The target values the fit aims at.
TARGET_SKY_ADU_PER_S = 3.0
TARGET_SEEING_ARCSEC = 5.0
EXPOSURE_SECONDS = 10.0
FRAMES = 3
#: How close the measured sky rate must be to the target, as a fraction. The
#: sky level is a median over a frame with stars, and the drivers truncate
#: each pixel to a whole number.
SKY_TOLERANCE = 0.15
#: How close the measured star width must be to the target, as a fraction.
#: A fitted width of a sampled Gaussian star is good to about this.
WIDTH_TOLERANCE = 0.25
CAMERA = "ZWO ASI533MM Pro"
#: The bias level in the camera profile above (also the frames' pedestal).
BIAS_LEVEL_ADU = 394.85
#: The made-up sensor is smaller than the real one so that a frame is quick.
SENSOR_PX = 1024

CAMERA_KEYS = {
    "sensor_width_px": f'"{SENSOR_PX}"',
    "sensor_height_px": f'"{SENSOR_PX}"',
    "sensor_noise": (
        '[{ gain_setting = "0", offset = "10", binning = "1x1", gain_e_per_adu = { value = 0.783 }, '
        "read_noise_e = { value = 3.8 } }]"
    ),
    "bias_levels": (
        '[{ gain_setting = "0", offset = "10", binning = "1x1", bias_level_adu = { value = 394.85 } }]'
    ),
}


def _candidate(kind: str, values: dict[str, float], note: str = "synthetic") -> Candidate:
    """Make a candidate for the test.

    Returns
    -------
    candidate : `Candidate`
        The candidate.
    """
    return Candidate(kind, values, "synthetic.call", {}, ["2026-10-03"], note, "2026-10-03")  # type: ignore[arg-type]


def _measurements() -> Measurements:
    """Make synthetic measurements for every row the simulators can take.

    Returns
    -------
    measurements : `Measurements`
        The candidates.
    """
    measurements = Measurements()
    rows = {
        "guide_rate": _candidate("newest real record", {"dec_rate_arcsec_per_s": 6.0}),
        "periodic_error": _candidate("history fit", {"amplitude_arcsec": 3.0, "period_seconds": 40.0}),
        "guiding_disturbance": _candidate(
            "history fit",
            {
                "ra_random_rms_arcsec": 0.2,
                "dec_random_rms_arcsec": 0.1,
                "ra_drift_arcsec_per_min": 0.6,
                "dec_drift_arcsec_per_min": 0.3,
            },
        ),
        "polar_error": _candidate("newest real record", {"total_error_arcsec": 600.0}),
        "dec_backlash": _candidate("history fit", {"backlash_ms": 400.0}),
        "seeing": _candidate("performance envelope", {"fwhm_arcsec": TARGET_SEEING_ARCSEC}),
        "sky_glow": _candidate("history fit", {"sky_rate_adu_per_s_per_px": TARGET_SKY_ADU_PER_S}),
    }
    for row, candidate in rows.items():
        measurements.add(row, candidate)
    return measurements


@pytest.fixture
def world(tmp_path: Path) -> Iterator[SimulatedObservatory]:
    """Start a simulated world with a fitted results file.

    Yields
    ------
    world : `SimulatedObservatory`
        The running world (stand-in guider, no PHD2).
    """
    root = tmp_path / "world"
    root.mkdir()
    indi_port = observatory.choose_free_port()
    phd2_port = observatory.choose_free_port()
    config_path = root / "astrometrics.config.toml"
    text = simulator_config_text(root, indi_port, phd2_port)
    for key, value in CAMERA_KEYS.items():
        text = observatory._set_key(text, f"Observatory.Camera.{CAMERA}", key, value)
    config_path.write_text(text, encoding="utf-8")
    setup = world_setup_from_settings(tomllib.loads(text), observatory.SIMULATED_TARGET_DEC_DEGREES)
    results_path = root / RESULTS_FILE_NAME
    write_results(results_path, fit_values(_measurements(), setup), fingerprint_library_copy(root))
    running = SimulatedObservatory(
        root,
        SimulationKind.SIMULATED_SKY,
        indi_port,
        phd2_port,
        exposure_seconds=EXPOSURE_SECONDS,
        guider=GuiderKind.MOCK,
        fitted_settings_path=results_path,
    )
    try:
        running.start()
        running.wait_ready(timeout=180.0)
        yield running
    finally:
        running.stop()


def _read_property(indi_port: int, device: str, property_name: str, element: str) -> str:
    """Read one INDI property element with ``indi_getprop``.

    Returns
    -------
    value : `str`
        The text the tool printed after the equals sign (empty if absent).
    """
    result = subprocess.run(
        [
            "indi_getprop",
            "-h",
            "127.0.0.1",
            "-p",
            str(indi_port),
            "-t",
            "3",
            f"{device}.{property_name}.{element}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return line.partition("=")[2]


def test_every_property_equals_the_value_in_the_results_file(world: SimulatedObservatory) -> None:
    """After start, each applied value is what the INDI server reports."""
    document = read_results(world.root / RESULTS_FILE_NAME)
    assert document is not None
    checked = 0
    for entry in document["values"]:
        if not entry["applied"] or entry["device"] == "PHD2 profile":
            continue
        # The driver sets the properties after the devices connect.
        deadline = time.time() + 30
        found = ""
        while time.time() < deadline:
            found = _read_property(world.indi_port, entry["device"], entry["property_name"], entry["element"])
            if matches(entry["value"], found, entry["display_decimals"]):
                break
            time.sleep(1)
        assert matches(entry["value"], found, entry["display_decimals"]), (
            f"{entry['device']}.{entry['property_name']}.{entry['element']}: "
            f"file {entry['value']}, server {found!r}"
        )
        checked += 1
    print(f"properties checked against the results file: {checked}")
    assert checked >= 20


def test_frames_measure_the_sky_and_star_width_the_fit_aimed_at(
    world: SimulatedObservatory, config_in_tmp_path: object
) -> None:
    """Short frames give the target sky rate and star width (see above)."""
    from astrometricslib import Astrometrics

    pictures = world.standin_home / "Pictures" / world.sky_target_name / "Light"
    deadline = time.time() + FRAMES * (EXPOSURE_SECONDS + 60) + 60
    while time.time() < deadline and len(list(pictures.glob("*.fits"))) < FRAMES:
        time.sleep(2)
    frames = sorted(pictures.glob("*.fits"))
    assert len(frames) >= FRAMES, f"only {len(frames)} frames appeared"
    astrometrics = Astrometrics(config_in_tmp_path, MagicMock())
    report = astrometrics.quality.measure_folder(
        str(pictures),
        limit=FRAMES,
        camera=CAMERA,
        bias_level_adu=BIAS_LEVEL_ADU,
        register_job=False,
    )
    gain_e_per_adu = 0.783
    sky_adu = report.medians["frame.sky_rate_e_per_s_per_px"] / gain_e_per_adu
    scale = 3.76 / 405.0 * 206.3
    target_width_px = TARGET_SEEING_ARCSEC / scale
    width_px = report.medians["frame.star_width_moffat_px"]
    print(f"CLOSURE sky rate: target {TARGET_SKY_ADU_PER_S:.3f} ADU/s/px, measured {sky_adu:.3f}")
    print(f"CLOSURE star width: target {target_width_px:.2f} px, measured {width_px:.2f} px")
    assert sky_adu == pytest.approx(TARGET_SKY_ADU_PER_S, rel=SKY_TOLERANCE)
    assert width_px == pytest.approx(target_width_px, rel=WIDTH_TOLERANCE)
