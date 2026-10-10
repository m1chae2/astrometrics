"""Purpose: Tests for the fit statistics read from solve-field's match table.

Description: solve-field writes a ``.corr`` table with one row per matched
star: the field star's fitted sky position and the reference star's sky
position. The driver turns that table into a fit residual (the root mean
square, RMS, of the distances) and a matched-star count, and attaches both to
the header it returns. solve-field is not installed where these tests run, so
they build fake ``.corr`` and ``.new`` files and a fake ``solve-field`` run.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers import astrometry_net_driver
from astrometricslib.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver
from astrometricslib.drivers.interfaces import plate_solve_driver

ARCSEC_DEG = 1.0 / 3600.0


def write_corr(
    path: Path,
    field_ra_deg: list[float],
    field_dec_deg: list[float],
    index_ra_deg: list[float],
    index_dec_deg: list[float],
) -> None:
    """Write a fake solve-field ``.corr`` table.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    field_ra_deg, field_dec_deg : `list` [`float`]
        The fitted sky positions of the matched field stars.
    index_ra_deg, index_dec_deg : `list` [`float`]
        The sky positions of the reference stars they matched.
    """
    columns = [
        fits.Column(name="field_ra", format="D", array=np.array(field_ra_deg)),
        fits.Column(name="field_dec", format="D", array=np.array(field_dec_deg)),
        fits.Column(name="index_ra", format="D", array=np.array(index_ra_deg)),
        fits.Column(name="index_dec", format="D", array=np.array(index_dec_deg)),
    ]
    fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns(columns)]).writeto(path)


def test_known_declination_offsets_give_the_expected_rms(tmp_path: Path) -> None:
    """Offsets of 1, 2, 2 and 0 arcsec give an RMS of exactly 1.5 arcsec."""
    corr_path = tmp_path / "input_image.corr"
    offsets_arcsec = np.array([1.0, 2.0, 2.0, 0.0])
    write_corr(
        corr_path,
        field_ra_deg=[10.0, 10.1, 10.2, 10.3],
        field_dec_deg=list(30.0 + offsets_arcsec * ARCSEC_DEG),
        index_ra_deg=[10.0, 10.1, 10.2, 10.3],
        index_dec_deg=[30.0, 30.0, 30.0, 30.0],
    )

    residual, count = astrometry_net_driver.read_corr_fit_statistics(str(corr_path))

    assert residual == pytest.approx(1.5, abs=1e-6)
    assert count == 4
    assert isinstance(residual, float)
    assert isinstance(count, int)


def test_a_right_ascension_offset_is_measured_on_the_sky_not_in_degrees(tmp_path: Path) -> None:
    """A 2 arcsec step in RA at declination 60 is only 1 arcsec on the sky."""
    corr_path = tmp_path / "input_image.corr"
    write_corr(
        corr_path,
        field_ra_deg=[100.0 + 2.0 * ARCSEC_DEG],
        field_dec_deg=[60.0],
        index_ra_deg=[100.0],
        index_dec_deg=[60.0],
    )

    residual, count = astrometry_net_driver.read_corr_fit_statistics(str(corr_path))

    assert residual == pytest.approx(1.0, abs=1e-3)
    assert count == 1


def test_a_pair_across_the_right_ascension_wrap_is_close(tmp_path: Path) -> None:
    """Positions 359.9999 and 0.0001 degrees apart are 0.72 arcsec apart."""
    corr_path = tmp_path / "input_image.corr"
    write_corr(corr_path, [359.9999], [0.0], [0.0001], [0.0])

    residual, _ = astrometry_net_driver.read_corr_fit_statistics(str(corr_path))

    assert residual == pytest.approx(0.72, abs=1e-3)


def test_rows_with_missing_positions_are_left_out(tmp_path: Path) -> None:
    """A NaN position drops that row from both the RMS and the count."""
    corr_path = tmp_path / "input_image.corr"
    write_corr(
        corr_path,
        field_ra_deg=[10.0, float("nan"), 10.2],
        field_dec_deg=[30.0 + 3.0 * ARCSEC_DEG, 30.0, 30.0 + 3.0 * ARCSEC_DEG],
        index_ra_deg=[10.0, 10.1, 10.2],
        index_dec_deg=[30.0, 30.0, 30.0],
    )

    residual, count = astrometry_net_driver.read_corr_fit_statistics(str(corr_path))

    assert residual == pytest.approx(3.0, abs=1e-6)
    assert count == 2


def test_an_empty_table_has_a_zero_count_and_no_residual(tmp_path: Path) -> None:
    """No matched stars means no RMS, and the count is a known zero."""
    corr_path = tmp_path / "input_image.corr"
    write_corr(corr_path, [], [], [], [])

    assert astrometry_net_driver.read_corr_fit_statistics(str(corr_path)) == (None, 0)


def test_a_missing_file_gives_unknown_statistics(tmp_path: Path) -> None:
    """No ``.corr`` file means the fit is unknown, not perfect."""
    assert astrometry_net_driver.read_corr_fit_statistics(str(tmp_path / "absent.corr")) == (None, None)


def test_a_table_without_position_columns_gives_unknown_statistics(tmp_path: Path) -> None:
    """A table that lacks the position columns cannot be measured."""
    corr_path = tmp_path / "input_image.corr"
    column = fits.Column(name="field_x", format="D", array=np.array([1.0, 2.0]))
    fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns([column])]).writeto(corr_path)

    assert astrometry_net_driver.read_corr_fit_statistics(str(corr_path)) == (None, None)


def test_a_file_that_is_not_fits_gives_unknown_statistics(tmp_path: Path) -> None:
    """A damaged file is reported as unknown, not raised."""
    corr_path = tmp_path / "input_image.corr"
    corr_path.write_text("not a fits file")

    assert astrometry_net_driver.read_corr_fit_statistics(str(corr_path)) == (None, None)


def fake_solve_field(monkeypatch: pytest.MonkeyPatch, write_corr_file: bool) -> None:
    """Replace the ``solve-field`` program with one that writes fake outputs.

    The fake writes ``input_image.new`` (a header with a sky map) in the
    working directory and, if asked, ``input_image.corr`` (a table whose
    offsets are 1, 2, 2 and 0 arcsec).

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace `subprocess.run` in the driver module.
    write_corr_file : `bool`
        Whether the fake program also writes the match table.
    """

    def run(command: list[str], **_keywords: Any) -> SimpleNamespace:
        """Write the output files into the directory named by ``--dir``.

        Parameters
        ----------
        command : `list` [`str`]
            The command line the driver built.
        **_keywords
            Ignored subprocess options.

        Returns
        -------
        completed : `types.SimpleNamespace`
            A successful run: exit status 0 and no output.
        """
        directory = Path(command[command.index("--dir") + 1])
        header = fits.Header({"CRVAL1": 10.7, "CRVAL2": 41.27, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN"})
        fits.PrimaryHDU(data=np.zeros((4, 4), dtype="float32"), header=header).writeto(
            directory / "input_image.new"
        )
        if write_corr_file:
            offsets = np.array([1.0, 2.0, 2.0, 0.0]) * ARCSEC_DEG
            write_corr(
                directory / "input_image.corr",
                [10.0, 10.1, 10.2, 10.3],
                list(30.0 + offsets),
                [10.0, 10.1, 10.2, 10.3],
                [30.0] * 4,
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(astrometry_net_driver.subprocess, "run", run)


def test_the_local_solve_returns_the_fit_statistics_with_the_wcs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The returned header keeps its sky map and adds the statistics."""
    fake_solve_field(monkeypatch, write_corr_file=True)

    header = AstrometryNetPlateSolveDriver()._run_solve_field(
        ["solve-field", "--dir", str(tmp_path), "input_image.fits"], str(tmp_path), timeout=10
    )

    assert isinstance(header, plate_solve_driver.PlateSolveHeader)
    assert header["CRVAL1"] == pytest.approx(10.7)
    assert plate_solve_driver.read_fit_statistics(header) == (pytest.approx(1.5, abs=1e-6), 4)
    # The statistics survive the copy that the callers make.
    assert plate_solve_driver.read_fit_statistics(header.copy()) == (pytest.approx(1.5, abs=1e-6), 4)


def test_the_local_solve_without_a_match_table_has_unknown_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a ``.corr`` file the solve works and the fit is unknown."""
    fake_solve_field(monkeypatch, write_corr_file=False)

    header = AstrometryNetPlateSolveDriver()._run_solve_field(
        ["solve-field", "--dir", str(tmp_path), "input_image.fits"], str(tmp_path), timeout=10
    )

    assert header is not None
    assert header["CRVAL2"] == pytest.approx(41.27)
    assert plate_solve_driver.read_fit_statistics(header) == (None, None)


def test_a_plain_header_reads_as_unknown_statistics() -> None:
    """A solver that returns an ordinary header says nothing about its fit."""
    assert plate_solve_driver.read_fit_statistics(fits.Header({"CRVAL1": 1.0})) == (None, None)
    assert plate_solve_driver.read_fit_statistics(None) == (None, None)
