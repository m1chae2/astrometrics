"""Tests for `CalibrationCatalog.assess` and `measure_frames`.

A small library of synthetic darks and biases is written under a temporary
folder and measured by the job. The camera is the shipped ASI 533 profile, so
the bias level (about 395 ADU at gain 0, offset 10) and the read noise
(about 4.85 ADU) come from the example configuration.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import CalibrationCatalog
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.gate_result import GateStatus

CAMERA_HEADER_NAME = "ZWO CCD ASI533MM Pro"
BIAS_LEVEL_ADU = 395.0
READ_NOISE_ADU = 4.85
VALUE_STEP = 4
FRAME_SHAPE = (150, 200)
FRAMES_PER_SET = 12


class _StubConfiguration:
    """A hashable configuration with the library in a temporary folder.

    The camera profiles come from the process-wide test configuration.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self.app_config = get_configuration().app_config

    def get_frames_path(self) -> Path:
        """Give the frames folder.

        Returns
        -------
        path : `pathlib.Path`
            The folder.
        """
        return self._root / "frames"

    def get_library_path(self) -> Path:
        """Give the folder of the databases.

        Returns
        -------
        path : `pathlib.Path`
            The folder.
        """
        return self._root / "library"

    def get_library_file_path(self, name: str) -> str:
        """Give the path of a file in the library folder.

        Returns
        -------
        path : `str`
            The path.
        """
        return str(self._root / "library" / name)

    def get_minimum_calibration_frames(self) -> int:
        """Give the minimum number of frames for a master.

        Returns
        -------
        minimum : `int`
            Three, the default.
        """
        return 3


def _write(path: Path, seed: int, level: float, band: float = 0.0, **cards: object) -> None:
    """Write one frame with noise and an optional bright band.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write.
    seed : `int`
        Seed of the noise.
    level : `float`
        Mean level in ADU.
    band : `float`, optional
        ADU added to the top third of the frame, a light leak.
    """
    generator = np.random.default_rng(seed)
    noisy = level + generator.normal(0.0, READ_NOISE_ADU, FRAME_SHAPE)
    noisy[: FRAME_SHAPE[0] // 3, :] += band
    header = fits.Header()
    header["INSTRUME"] = CAMERA_HEADER_NAME
    header["GAIN"] = 0
    header["OFFSET"] = 10
    for key, value in cards.items():
        header[key.replace("_", "-")] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = (np.round(noisy / VALUE_STEP) * VALUE_STEP).astype(np.uint16)
    fits.PrimaryHDU(pixels, header=header).writeto(path, overwrite=True)


@pytest.fixture
def catalog(tmp_path: Path) -> CalibrationCatalog:
    """Make a catalog over a library of 12 biases and 12 darks.

    The last dark has a bright band across its top third.

    Returns
    -------
    catalog : `CalibrationCatalog`
        The catalog, with the frames indexed.
    """
    for index in range(FRAMES_PER_SET):
        _write(tmp_path / "frames" / "biases" / f"Bias_{index}.fits", index, BIAS_LEVEL_ADU, EXPTIME=0.0)
        band = 80.0 if index == FRAMES_PER_SET - 1 else 0.0
        _write(
            tmp_path / "frames" / "darks" / f"Dark_{index}.fits",
            100 + index,
            BIAS_LEVEL_ADU + 0.06,
            band,
            EXPTIME=30.0,
            CCD_TEMP=-10.0,
        )
    catalog = CalibrationCatalog(_StubConfiguration(tmp_path), None)
    catalog.reindex_frames("bias")
    catalog.reindex_frames("dark")
    return catalog


def _statuses(health: object) -> dict[str, GateStatus]:
    """Index the checks of one set by name.

    Returns
    -------
    statuses : `dict`
        Check name to status.
    """
    return {check.name: check.status for check in health.checks}


def test_before_measuring_every_check_but_the_count_is_not_checked(catalog: CalibrationCatalog) -> None:
    """Numbers that are not stored yet are not judged, and the set says so."""
    (health,) = catalog.assess("bias", register_job=False)
    assert health.status == "good"
    statuses = _statuses(health)
    assert statuses["calibration_frame_count"] is GateStatus.PASSED
    assert statuses["bias_noise"] is GateStatus.NOT_CHECKED
    assert health.measured_frame_count == 0
    assert "not measured yet" in health.summary


def test_a_measured_bias_set_is_good(catalog: CalibrationCatalog) -> None:
    """Twelve clean biases at the profile level and noise pass."""
    report = catalog.measure_frames(register_job=False)
    assert report.measured_count == 2 * FRAMES_PER_SET
    (health,) = catalog.assess("bias", register_job=False)
    assert health.status == "good"
    assert health.measured_frame_count == FRAMES_PER_SET
    assert set(_statuses(health).values()) == {GateStatus.PASSED}
    assert health.row_key.startswith("bias|")


def test_the_light_struck_dark_is_found_and_the_set_warns(catalog: CalibrationCatalog) -> None:
    """The dark with the bright band fails the light leak check."""
    catalog.measure_frames(register_job=False)
    (health,) = catalog.assess("dark", register_job=False)
    statuses = _statuses(health)
    assert statuses["dark_light_leak"] is GateStatus.FAILED
    assert statuses["dark_zero_pixel_fraction"] is GateStatus.PASSED
    assert health.status == "warning"
    assert [Path(concern.path).name for concern in health.frames_of_concern] == [
        f"Dark_{FRAMES_PER_SET - 1}.fits"
    ]
    assert health.exposure_seconds == pytest.approx(30.0)


def test_the_row_key_matches_the_counts_row(catalog: CalibrationCatalog) -> None:
    """The display can join the health to the counts row by the same key."""
    counts = catalog.query(kind="dark")
    (health,) = catalog.assess("dark", register_job=False)
    assert [row["row_key"] for row in counts.darks] == [health.row_key]


def test_a_second_measuring_run_reads_nothing(catalog: CalibrationCatalog) -> None:
    """Unchanged files are skipped by their stamps."""
    catalog.measure_frames(register_job=False)
    again = catalog.measure_frames(register_job=False)
    assert again.measured_count == 0
    assert again.unchanged_count == 2 * FRAMES_PER_SET


def test_assess_narrows_by_setting_and_refuses_an_argument_of_another_kind(
    catalog: CalibrationCatalog,
) -> None:
    """A matching exposure finds the set; a wrong argument is refused."""
    assert len(catalog.assess("dark", exposure_seconds=30.0, register_job=False)) == 1
    assert catalog.assess("dark", exposure_seconds=60.0, register_job=False) == []
    assert catalog.assess("bias", offset=20, register_job=False) == []
    with pytest.raises(InvalidArgumentError, match="telescope"):
        catalog.assess("bias", telescope="Scope", register_job=False)
    with pytest.raises(InvalidArgumentError, match="exposure_seconds"):
        catalog.assess("bias", exposure_seconds=30.0, register_job=False)
    with pytest.raises(InvalidArgumentError, match="kind"):
        catalog.assess("lamp", register_job=False)  # ty: ignore[invalid-argument-type]
