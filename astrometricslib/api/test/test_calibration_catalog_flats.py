"""Tests for `CalibrationCatalog.refresh` reports and `assess_flats`.

They build a small flat library on disk with a stub configuration, the way
a download from the telescope computer would leave it.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import CalibrationCatalog

CAMERA = "ZWO ASI 533MM Pro"


def write_flat(path: Path, seed: int, filter_name: str, offset: int = 10, level: float = 0.45) -> None:
    """Write a quiet 16-bit flat frame with the headers Ekos records.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write. Its folder is created.
    seed : `int`
        Seed of the noise.
    filter_name : `str`
        The ``FILTER`` header value.
    offset : `int`, optional
        The ``OFFSET`` header value.
    level : `float`, optional
        Mean brightness as a fraction of full scale.
    """
    generator = np.random.default_rng(seed)
    data = level * 65535.0 * (1.0 + 0.007 * generator.standard_normal((64, 64)))
    header = fits.Header()
    header["INSTRUME"] = CAMERA
    header["FILTER"] = filter_name
    header["GAIN"] = 0
    header["OFFSET"] = offset
    header["IMAGETYP"] = "Flat Frame"
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, np.clip(data, 0, 65535).astype(np.uint16), header, overwrite=True)


@pytest.fixture
def catalog(tmp_path: Path) -> CalibrationCatalog:
    """Make a catalog whose library lives under a temporary folder.

    Returns
    -------
    catalog : `CalibrationCatalog`
        A catalog on a stub configuration.
    """
    configuration = SimpleNamespace(
        get_frames_path=lambda: tmp_path / "frames",
        get_library_file_path=lambda name: str(tmp_path / "library" / name),
    )
    return CalibrationCatalog(configuration)


def flat_folder(tmp_path: Path, filter_folder: str) -> Path:
    """Give the folder flats of one filter are downloaded into.

    Returns
    -------
    folder : `pathlib.Path`
        The folder, laid out telescope / camera / filter.
    """
    return tmp_path / "frames" / "flats" / "Scope" / CAMERA / filter_folder


def test_refresh_reports_new_flats_and_assesses_them(catalog: CalibrationCatalog, tmp_path: Path) -> None:
    """A rescan after a download names the new set and measures it."""
    for index in range(6):
        write_flat(flat_folder(tmp_path, "L") / f"flat_{index}.fits", index, "Luminance")

    report = catalog.refresh("flat")

    assert report.kind == "flat"
    assert report.added_count == 6
    assert report.removed_count == 0
    assert len(report.changes) == 1
    assert report.changes[0].added == 6
    assert len(report.flat_assessments) == 1
    assessment = report.flat_assessments[0]
    assert assessment.frame_count == 6
    assert assessment.offset == pytest.approx(10.0)
    assert assessment.passes


def test_second_refresh_with_no_new_frames_reports_nothing_added(
    catalog: CalibrationCatalog, tmp_path: Path
) -> None:
    """Rescanning an unchanged folder adds nothing and measures nothing."""
    for index in range(3):
        write_flat(flat_folder(tmp_path, "L") / f"flat_{index}.fits", index, "Luminance")
    catalog.refresh("flat")

    report = catalog.refresh("flat")

    assert report.added_count == 0
    assert report.changes == []
    assert report.flat_assessments == []
    assert report.total_count == 3


def test_refresh_assesses_each_filter_and_offset_separately(
    catalog: CalibrationCatalog, tmp_path: Path
) -> None:
    """Flats at another offset are a separate set, not mixed in."""
    for index in range(3):
        write_flat(flat_folder(tmp_path, "L") / f"flat_{index}.fits", index, "Luminance")
        write_flat(flat_folder(tmp_path, "L") / f"old_{index}.fits", 20 + index, "Luminance", offset=0)
        write_flat(flat_folder(tmp_path, "SPEC") / f"flat_{index}.fits", 40 + index, "Spectroscopy")

    report = catalog.refresh("flat")

    keys = sorted((a.filter, a.offset) for a in report.flat_assessments)
    assert keys == [("Luminance", 0.0), ("Luminance", 10.0), ("Star Analyzer 200", 10.0)]
    assert report.added_count == 9


def test_assess_flats_narrows_by_filter_and_settings(catalog: CalibrationCatalog, tmp_path: Path) -> None:
    """Giving a filter alias and a gain/offset picks only that set."""
    for index in range(3):
        write_flat(flat_folder(tmp_path, "L") / f"flat_{index}.fits", index, "Luminance")
        write_flat(flat_folder(tmp_path, "L") / f"old_{index}.fits", 20 + index, "Luminance", offset=0)
        write_flat(flat_folder(tmp_path, "SPEC") / f"flat_{index}.fits", 40 + index, "Spectroscopy")
    catalog.refresh("flat")

    luminance = catalog.assess_flats(filter_type="L", gain=0, offset=10)
    spectroscopy = catalog.assess_flats(filter_type="SPEC")
    everything = catalog.assess_flats()

    assert [(a.filter, a.frame_count) for a in luminance] == [("Luminance", 3)]
    assert [a.frame_count for a in spectroscopy] == [3]
    assert len(everything) == 3


def test_assess_flats_with_nothing_matching_is_empty(catalog: CalibrationCatalog) -> None:
    """An empty library gives an empty list, not an error."""
    assert catalog.assess_flats(filter_type="L") == []


def test_refresh_rejects_an_unknown_kind(catalog: CalibrationCatalog) -> None:
    """A kind other than dark, bias or flat is refused."""
    with pytest.raises(ValueError, match="Unknown calibration kind"):
        catalog.refresh("lamp")  # ty: ignore[invalid-argument-type]
