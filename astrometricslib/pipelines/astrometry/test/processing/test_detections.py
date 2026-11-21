"""Tests of the viewer's detection list.

They check that `measure_detections_in_file` reports the stars of a synthetic
image in 1-based pixels, that it gives the same list as the function the
astrometry run calls (`find_unique_stars`), that a star reported twice is
marked as merged, and that the photometry circle is sized from the image's
own star width. They also check the saved lists: the cache and a stack's
sidecar are used while the file and the finder settings are unchanged, a
changed file or setting finds the stars again, and the overlay measures no
per-star radius.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import Astrometrics
from astrometricslib.exceptions import NotFoundError
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing import detections as detections_module
from astrometricslib.pipelines.astrometry.processing.detections import (
    measure_detections_in_file,
    save_run_detections,
)
from astrometricslib.pipelines.astrometry.processing.star_identifier import find_unique_stars

STAR_POSITIONS = [(40.0, 50.0), (120.0, 60.0), (80.0, 140.0), (150.0, 150.0)]
CLOSE_PAIR = [(60.0, 100.0), (66.0, 100.0)]


def _write_image(path: Path, positions: list[tuple[float, float]]) -> np.ndarray:
    """Write a noisy image with Gaussian stars at the given 0-based positions.

    Returns
    -------
    image : `numpy.ndarray`
        The pixels that were written.
    """
    rows, columns = np.mgrid[0:200, 0:200]
    image = np.random.default_rng(3).normal(100.0, 2.0, size=(200, 200))
    for x, y in positions:
        image += 3000.0 * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * 1.7**2))
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    return image


def test_stars_are_found_in_one_based_pixels(tmp_path: Path) -> None:
    """Each known star has a detection within a pixel of its position."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    result = measure_detections_in_file(str(path))
    assert result.detection_count == len(result.detections)
    assert result.width == 200 and result.height == 200
    for x, y in STAR_POSITIONS:
        nearest = min(result.detections, key=lambda d: (d.x - (x + 1)) ** 2 + (d.y - (y + 1)) ** 2)
        assert abs(nearest.x - (x + 1)) < 1.0
        assert abs(nearest.y - (y + 1)) < 1.0
    assert type(result.detections[0].x) is float
    assert result.kernel_fwhm_px > 0


def test_same_list_as_the_run_function(tmp_path: Path) -> None:
    """The call gives the run function's list, shifted to 1-based pixels."""
    path = tmp_path / "pair.fits"
    image = _write_image(path, STAR_POSITIONS + CLOSE_PAIR)
    result = measure_detections_in_file(str(path))
    _, unique = find_unique_stars(
        SourceDetector(keep_close_pairs=True), np.asarray(image, dtype=np.float32).astype(float)
    )
    assert len(unique) == result.detection_count
    for source, detection in zip(unique, result.detections, strict=True):
        assert detection.x == pytest.approx(float(source["x_centroid"]) + 1.0)
        assert detection.y == pytest.approx(float(source["y_centroid"]) + 1.0)
        assert detection.flux == pytest.approx(float(source["flux"]))


def test_a_star_reported_twice_is_marked_merged() -> None:
    """Two close reports become one marked detection."""
    detector = SourceDetector(fwhm=4.0)
    sources = [
        {"x_centroid": 10.0, "y_centroid": 10.0, "flux": 5.0},
        {"x_centroid": 11.0, "y_centroid": 10.0, "flux": 2.0},
        {"x_centroid": 50.0, "y_centroid": 50.0, "flux": 3.0},
    ]
    unique = detector.deduplicate(sources)
    assert len(unique) == 2
    merged = unique[0]
    assert merged["merged_detection_count"] == 2
    assert merged["x_centroid"] == pytest.approx(10.0)
    assert merged["flux"] == pytest.approx(7.0)
    assert "merged_detection_count" not in unique[1]


def test_public_call_matches_and_missing_file_is_refused(tmp_path: Path) -> None:
    """The public call gives the same count; a missing file is refused."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    through_api = Astrometrics().quality.measure_detections(str(path))
    assert through_api.detection_count == measure_detections_in_file(str(path)).detection_count
    with pytest.raises(NotFoundError):
        measure_detections_in_file(str(tmp_path / "missing.fits"))


def test_circle_is_sized_from_the_image_star_width(tmp_path: Path) -> None:
    """The circle follows the star width measured on this image."""
    from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import APERTURE_RADIUS_PX

    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    through_api = Astrometrics().quality.measure_detections(str(path))
    # The stars are Gaussians of sigma 1.7 px: a 4.0 px FWHM.
    assert through_api.star_width_px == pytest.approx(4.0, rel=0.15)
    factor = float(APERTURE_RADIUS_PX.default)  # the radius per star width
    assert through_api.aperture_radius_px == pytest.approx(factor * through_api.star_width_px)


@pytest.fixture
def private_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the app's scratch folder at an empty folder.

    Returns
    -------
    folder : `pathlib.Path`
        The scratch folder.
    """
    folder = tmp_path / "scratch"
    folder.mkdir()
    monkeypatch.setattr(detections_module, "scratch_directory", lambda: str(folder))
    return folder


def _count_finder_calls(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Wrap the finder so each call is recorded.

    Returns
    -------
    calls : `list` [`bool`]
        One entry per call, holding the ``with_radius`` it was given.
    """
    calls: list[bool] = []
    real = detections_module.find_unique_stars

    def counting(*args: object, **kwargs: object) -> object:
        """Record the call, then run the real finder.

        Returns
        -------
        result : `object`
            What the real finder returns.
        """
        calls.append(bool(kwargs.get("with_radius", True)))
        return real(*args, **kwargs)

    monkeypatch.setattr(detections_module, "find_unique_stars", counting)
    return calls


def test_overlay_skips_per_star_radius(
    tmp_path: Path, private_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overlay never measures a star's own radius."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    calls = _count_finder_calls(monkeypatch)

    def refuse(*args: object, **kwargs: object) -> None:
        """Fail the test if a per-star radius is measured.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("per-star radius measured")

    monkeypatch.setattr(
        "astrometricslib.pipelines.astrometry.pre_processing.source_detection.measure_star_radius_px", refuse
    )
    measure_detections_in_file(str(path))
    assert calls == [False]


def test_cache_hit_skips_the_finder_and_equals_recomputing(
    tmp_path: Path, private_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second call reads the cache, with the same result as the first."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS + CLOSE_PAIR)
    calls = _count_finder_calls(monkeypatch)
    first = measure_detections_in_file(str(path))
    second = measure_detections_in_file(str(path))
    assert len(calls) == 1
    assert second == first
    assert second == detections_module._find_detections(str(path))
    assert (private_cache / "detections").is_dir()


def test_changed_file_or_settings_recompute(
    tmp_path: Path, private_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new file time or a changed finder setting finds the stars again."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    calls = _count_finder_calls(monkeypatch)
    measure_detections_in_file(str(path))
    status = path.stat()
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 5_000_000_000))
    measure_detections_in_file(str(path))
    assert len(calls) == 2
    measure_detections_in_file(str(path))
    assert len(calls) == 2
    monkeypatch.setattr(detections_module, "_finder_settings", lambda: {"threshold_sigma": -1.0})
    measure_detections_in_file(str(path))
    assert len(calls) == 3


def test_stack_sidecar_is_used_and_raw_frames_get_none(
    tmp_path: Path, private_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack gets a saved list that is read back; a frame gets none."""
    stack = tmp_path / "Stack.fits"
    _write_image(stack, STAR_POSITIONS)
    with fits.open(stack, mode="update") as hdul:
        hdul[0].header["STACKCNT"] = 10
    frame = tmp_path / "Frame.fits"
    _write_image(frame, STAR_POSITIONS)
    assert save_run_detections(str(stack))
    assert not save_run_detections(str(frame))
    assert (tmp_path / "Stack_detections.json").exists()
    assert not (tmp_path / "Frame_detections.json").exists()
    calls = _count_finder_calls(monkeypatch)
    loaded = measure_detections_in_file(str(stack))
    assert calls == []
    assert loaded.detection_count == len(detections_module._find_detections(str(stack)).detections)
    assert loaded.detections == detections_module._find_detections(str(stack)).detections
    calls.clear()
    # Replacing the stack makes the saved list stale.
    status = stack.stat()
    os.utime(stack, ns=(status.st_atime_ns, status.st_mtime_ns + 5_000_000_000))
    measure_detections_in_file(str(stack))
    assert len(calls) == 1


def test_close_pair_is_two_stars_for_viewing_and_one_for_solving(tmp_path: Path) -> None:
    """A close pair is two stars for viewing and one for solving."""
    path = tmp_path / "pair.fits"
    image = np.asarray(_write_image(path, [(100.0, 100.0), (106.0, 100.0)]), dtype=np.float32).astype(float)
    # Make the companion fainter, like the real case.
    rows, columns = np.mgrid[0:200, 0:200]
    image -= 2000.0 * np.exp(-((columns - 106.0) ** 2 + (rows - 100.0) ** 2) / (2 * 1.7**2))
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path, overwrite=True)
    data = np.asarray(image, dtype=np.float32).astype(float)
    _, solving = find_unique_stars(SourceDetector(), data)
    _, viewing = find_unique_stars(SourceDetector(keep_close_pairs=True), data)
    assert len(solving) == 1
    assert len(viewing) == 2


def test_saved_list_is_stale_when_the_viewing_separation_changes(
    tmp_path: Path, private_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewing separation is part of the stamp, so old lists recompute."""
    assert "viewing_separation_kernel_widths" in detections_module._finder_settings()
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    calls = _count_finder_calls(monkeypatch)
    measure_detections_in_file(str(path))
    settings = dict(detections_module._finder_settings(), viewing_separation_kernel_widths=9.0)
    monkeypatch.setattr(detections_module, "_finder_settings", lambda: settings)
    measure_detections_in_file(str(path))
    assert len(calls) == 2
