"""Tests for the GraXpert background removal of the stack preview.

GraXpert is not started. A stand-in replaces the `run_registered` call that
would run it, so the tests check the command line it would get.
"""

from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import preview_graxpert, stack_preview
from astrometricslib.pipelines.stacking.post_processing.preview_graxpert import (
    flatten_background,
    flatten_in_stack_units,
    scale_into_unit_range,
)


def test_graxpert_is_asked_for_a_subtracted_ai_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the GraXpert command line asks for AI background subtraction."""
    captured: dict = {}

    def fake_run(arguments: list[str], _timeout_seconds: float) -> stack_preview.subprocess.CompletedProcess:
        """Record the command line.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A successful result.
        """
        captured["arguments"] = arguments
        return stack_preview.subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(preview_graxpert, "run_registered", fake_run)
    monkeypatch.setattr(preview_graxpert, "siril_process_lock", nullcontext)

    assert flatten_background("/opt/graxpert-gpu.sh", "/work/stack.fits", "/work/flat")

    arguments = captured["arguments"]
    assert arguments[0] == "/opt/graxpert-gpu.sh"
    assert arguments[1:5] == ["-cli", "-cmd", "background-extraction", "-gpu"]
    assert arguments[arguments.index("-correction") + 1] == "Subtraction"
    assert arguments[-3:] == ["-output", "/work/flat", "/work/stack.fits"]
    assert "-bg" not in arguments


def _write(path: Path, data: np.ndarray) -> None:
    """Write a small image with a header card that must survive.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to write.
    data : `numpy.ndarray`
        The pixels.
    """
    header = fits.Header()
    header["OBJECT"] = "test"
    fits.writeto(path, data.astype(np.float32), header, overwrite=True)


def test_a_stack_with_a_bright_pixel_is_divided_by_its_brightest_pixel(tmp_path: Path) -> None:
    """The copy runs up to 1.0 and the factor is the brightest pixel."""
    data = np.full((8, 8), 0.04)
    data[3, 3] = 1.28
    _write(tmp_path / "in.fits", data)

    factor = scale_into_unit_range(str(tmp_path / "in.fits"), str(tmp_path / "scaled.fits"))

    assert factor == pytest.approx(1.28)
    with fits.open(tmp_path / "scaled.fits", memmap=False) as hdul:
        assert hdul[0].data.max() == pytest.approx(1.0)
        assert hdul[0].header["OBJECT"] == "test"


def test_a_stack_at_or_below_one_is_left_alone(tmp_path: Path) -> None:
    """No copy is made and the factor is 1."""
    _write(tmp_path / "in.fits", np.full((8, 8), 0.5))

    factor = scale_into_unit_range(str(tmp_path / "in.fits"), str(tmp_path / "scaled.fits"))

    assert factor == pytest.approx(1.0)
    assert not (tmp_path / "scaled.fits").exists()


def test_a_file_that_is_not_an_image_is_passed_on_unchanged(tmp_path: Path) -> None:
    """Unreadable input gives factor 1 and no copy, so GraXpert reports it."""
    (tmp_path / "in.fits").write_bytes(b"not fits")

    assert scale_into_unit_range(str(tmp_path / "in.fits"), str(tmp_path / "s.fits")) == pytest.approx(1.0)


def test_the_picture_comes_back_in_the_stacks_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GraXpert reads a 0 to 1 copy; its picture is scaled back."""
    data = np.full((8, 8), 0.04)
    data[3, 3] = 2.0
    _write(tmp_path / "stack.fits", data)
    seen: dict = {}

    def fake_flatten(executable: str, input_path: str, output_stem: str) -> bool:
        """Record what GraXpert would read and write a picture in 0 to 1.

        Returns
        -------
        succeeded : `bool`
            Always `True`.
        """
        seen["max"] = float(fits.getdata(input_path, memmap=False).max())
        seen["input"] = input_path
        _write(Path(f"{output_stem}.fits"), fits.getdata(input_path, memmap=False) - 0.01)
        return True

    monkeypatch.setattr(preview_graxpert, "flatten_background", fake_flatten)

    assert flatten_in_stack_units("gx", str(tmp_path / "stack.fits"), str(tmp_path / "flat.fits"))

    assert seen["max"] == pytest.approx(1.0)
    assert seen["input"] != str(tmp_path / "stack.fits")
    flat = fits.getdata(tmp_path / "flat.fits", memmap=False)
    assert flat.max() == pytest.approx(2.0 - 0.01 * 2.0, rel=1e-5)
    assert flat[0, 0] == pytest.approx(0.04 - 0.02, rel=1e-4)
    assert fits.getheader(tmp_path / "flat.fits")["OBJECT"] == "test"
    assert not Path(seen["input"]).exists()


def test_a_stack_in_range_goes_to_graxpert_as_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing above 1.0 the input file and the output are not touched."""
    _write(tmp_path / "stack.fits", np.full((8, 8), 0.5))
    seen: dict = {}

    def fake_flatten(executable: str, input_path: str, output_stem: str) -> bool:
        """Write a picture and record the input.

        Returns
        -------
        succeeded : `bool`
            Always `True`.
        """
        seen["input"] = input_path
        _write(Path(f"{output_stem}.fits"), np.full((8, 8), 0.25))
        return True

    monkeypatch.setattr(preview_graxpert, "flatten_background", fake_flatten)

    assert flatten_in_stack_units("gx", str(tmp_path / "stack.fits"), str(tmp_path / "flat.fits"))

    assert seen["input"] == str(tmp_path / "stack.fits")
    assert fits.getdata(tmp_path / "flat.fits", memmap=False).max() == pytest.approx(0.25)


def test_a_failed_graxpert_leaves_no_copy_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The unit-range copy is deleted even when GraXpert fails."""
    data = np.full((8, 8), 0.04)
    data[0, 0] = 3.0
    _write(tmp_path / "stack.fits", data)
    monkeypatch.setattr(preview_graxpert, "flatten_background", lambda *arguments: False)

    assert not flatten_in_stack_units("gx", str(tmp_path / "stack.fits"), str(tmp_path / "flat.fits"))

    assert sorted(path.name for path in tmp_path.iterdir()) == ["stack.fits"]
