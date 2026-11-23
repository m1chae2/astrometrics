"""Tests for the list of nebulae and galaxies in a stack's field.

A stand-in SIMBAD driver answers the query, so the tests need no network.
They check that objects outside the stack's pixels and objects of the wrong
kind are dropped, that the nearest to the center comes first, that a stack
with no plate solution says to run Astrometry first, that an unreachable
SIMBAD is a message, not an error, and that the list is saved beside the
stack and reused until the stack file changes.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib._internal.drivers.cached_simbad_driver import CachedSimbadDriver
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.target import PreviewBrightObject, Target
from astrometricslib.pipelines.stacking.post_processing import bright_object_choices
from astrometricslib.pipelines.stacking.post_processing.bright_object_choices import (
    common_name_rank,
    list_bright_object_choices,
)

SIZE = 200
SCALE_ARCSEC = 10.0
CENTER_RA = 350.0
CENTER_DEC = 61.0


class FakeSimbad(SimbadDriver):
    """A SIMBAD stand-in with a fixed answer that counts the questions."""

    def __init__(
        self,
        table: Table | None = None,
        failure: Exception | None = None,
        identifiers: Table | None = None,
    ) -> None:
        """Keep the answer to give.

        Parameters
        ----------
        table : `astropy.table.Table` or `None`, optional
            The rows ``query_tap`` returns.
        failure : `Exception`, optional
            An error ``query_tap`` raises instead.
        identifiers : `astropy.table.Table` or `None`, optional
            The rows ``query_tap`` returns for a question about identifiers.
        """
        self.identifiers = identifiers
        self.table = table
        self.failure = failure
        self.queries: list[str] = []

    def query_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
    ) -> Any:
        """Not used by the list.

        Returns
        -------
        result_table : `None`
            Always `None`.
        """
        return None

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Not used by the list.

        Returns
        -------
        result_table : `None`
            Always `None`.
        """
        return None

    def query_tap(self, adql_query: str) -> Any:
        """Record the question and give the fixed answer.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The table given at construction.

        """
        self.queries.append(adql_query)
        if "FROM ident" in adql_query:
            return self.identifiers
        if self.failure is not None:
            raise self.failure
        return self.table


def rows(*entries: tuple[str, float, float, str, float | None]) -> Table:
    """Build a SIMBAD-like table.

    Parameters
    ----------
    *entries : `tuple`
        Name, right ascension, declination, type code and major axis.

    Returns
    -------
    table : `astropy.table.Table`
        The table, with a masked major axis where the entry has none.
    """
    table = Table({
        "main_id": [entry[0] for entry in entries],
        "ra": [entry[1] for entry in entries],
        "dec": [entry[2] for entry in entries],
        "otype": [entry[3] for entry in entries],
        "major_axis": np.ma.masked_invalid([np.nan if entry[4] is None else entry[4] for entry in entries]),
    })
    return table


def rows_with_ids(*entries: tuple[int, str, float, float, str, float | None]) -> Table:
    """Build a SIMBAD-like table that also has object numbers.

    Parameters
    ----------
    *entries : `tuple`
        Object number, name, right ascension, declination, type code and
        major axis.

    Returns
    -------
    table : `astropy.table.Table`
        The table, with an ``oid`` column.
    """
    table = rows(*[entry[1:] for entry in entries])
    table["oid"] = [entry[0] for entry in entries]
    return table


def write_stack(path: Path, solved: bool = True) -> str:
    """Write a small stack, with or without a plate solution.

    Returns
    -------
    path : `str`
        The file's path.
    """
    header = fits.Header()
    if solved:
        wcs = WCS(naxis=2)
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        wcs.wcs.crval = [CENTER_RA, CENTER_DEC]
        wcs.wcs.crpix = [SIZE / 2 + 0.5, SIZE / 2 + 0.5]
        wcs.wcs.cdelt = [-SCALE_ARCSEC / 3600.0, SCALE_ARCSEC / 3600.0]
        header = wcs.to_header()
    fits.PrimaryHDU(np.zeros((SIZE, SIZE), dtype=np.float32), header=header).writeto(path)
    return str(path)


def test_the_list_keeps_nebulae_and_galaxies_inside_the_frame_nearest_the_center_first(
    tmp_path: Path,
) -> None:
    """Wrong kinds and off-picture objects go; the rest sort by distance."""
    offset = 0.2
    simbad = FakeSimbad(
        rows(
            ("Far nebula", CENTER_RA, CENTER_DEC + offset, "HII", 10.0),
            ("Near nebula", CENTER_RA, CENTER_DEC + 0.02, "HII", 9.0),
            ("NGC 1", CENTER_RA + 0.3, CENTER_DEC, "G", None),
            ("Off the picture", CENTER_RA + 3.0, CENTER_DEC, "G", 5.0),
            ("A cluster", CENTER_RA, CENTER_DEC, "OpC", 20.0),
        )
    )
    result = list_bright_object_choices("M 52", write_stack(tmp_path / "stack.fits"), simbad)

    assert result.message is None
    by_name = {entry.simbad_id: entry for entry in result.objects}
    assert sorted(by_name) == ["Far nebula", "NGC 1", "Near nebula"]
    assert by_name["Near nebula"].category == "nebula"
    assert by_name["NGC 1"].category == "galaxy"
    assert by_name["NGC 1"].major_axis_arcmin is None
    assert "CIRCLE('ICRS'" in simbad.queries[0]


def test_a_stack_without_a_plate_solution_says_to_run_astrometry_first(tmp_path: Path) -> None:
    """No solution means no list, and SIMBAD is not asked."""
    simbad = FakeSimbad(rows())
    result = list_bright_object_choices("M 52", write_stack(tmp_path / "stack.fits", solved=False), simbad)

    assert result.objects == []
    assert result.message == "Run Astrometry first to list objects in the frame"
    assert simbad.queries == []


def test_a_target_with_no_stack_gives_a_message(tmp_path: Path) -> None:
    """A missing stack file is a message, not an error."""
    result = list_bright_object_choices("M 52", str(tmp_path / "missing.fits"), FakeSimbad())

    assert result.objects == []
    assert result.message == bright_object_choices.MESSAGE_NO_STACK


def test_simbad_out_of_reach_is_a_message_not_an_error(tmp_path: Path) -> None:
    """An unreachable service gives no objects and a plain message."""
    simbad = FakeSimbad(failure=ExternalServiceError("down"))
    result = list_bright_object_choices("M 52", write_stack(tmp_path / "stack.fits"), simbad)

    assert result.objects == []
    assert result.message == bright_object_choices.MESSAGE_SIMBAD_UNREACHABLE


def test_the_list_is_stored_and_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call reads SIMBAD's stored answer; no question goes out."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    stack = write_stack(tmp_path / "stack.fits")
    fake = FakeSimbad(rows(("Near nebula", CENTER_RA, CENTER_DEC, "HII", 9.0)))
    simbad = CachedSimbadDriver(fake)

    first = list_bright_object_choices("M 52", stack, simbad)
    asked = len(fake.queries)
    second = list_bright_object_choices("M 52", stack, simbad)

    assert first.from_cache is False
    assert second.from_cache is True
    assert [entry.simbad_id for entry in second.objects] == ["Near nebula"]
    assert len(fake.queries) == asked


def test_refresh_style_call_skips_the_stored_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``use_cache`` off, SIMBAD is asked though an answer is stored."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    stack = write_stack(tmp_path / "stack.fits")
    fake = FakeSimbad(rows(("Near nebula", CENTER_RA, CENTER_DEC, "HII", 9.0)))
    simbad = CachedSimbadDriver(fake)
    list_bright_object_choices("M 52", stack, simbad)
    asked = len(fake.queries)
    list_bright_object_choices("M 52", stack, simbad, use_cache=False)

    assert len(fake.queries) > asked


def test_the_target_record_defaults_to_automatic_and_loads_without_the_field() -> None:
    """A target saved before the choice existed reads as Automatic."""
    assert Target(id="M 52").preview_bright_object.mode == "auto"
    loaded = Target.model_validate({"id": "M 52", "previewBrightObject": {"mode": "off"}})
    assert loaded.preview_bright_object == PreviewBrightObject(mode="off")
    with pytest.raises(ValueError, match="mode"):
        PreviewBrightObject(mode="sometimes")


def test_hii_regions_inside_a_galaxy_are_dropped_and_the_galaxy_takes_its_common_name(
    tmp_path: Path,
) -> None:
    """Sub-components go, a separate neighbor stays, names are common."""
    simbad = FakeSimbad(
        rows_with_ids(
            (1, "NGC  7331", CENTER_RA, CENTER_DEC, "LIN", 10.5),
            (2, "[P98] 175", CENTER_RA + 0.01, CENTER_DEC + 0.01, "HII", None),
            (3, "[P98] 85", CENTER_RA - 0.02, CENTER_DEC, "HII", 0.5),
            (4, "Big cloud", CENTER_RA + 0.02, CENTER_DEC, "HII", 6.0),
            (5, "[ABB] tiny", CENTER_RA - 0.2, CENTER_DEC, "HII", 0.3),
            (6, "[ABB] big", CENTER_RA + 0.2, CENTER_DEC, "HII", 8.0),
            (7, "SH 2-162", CENTER_RA, CENTER_DEC - 0.2, "HII", None),
        ),
        identifiers=Table({
            "oidref": [1, 1, 7, 7],
            "id": ["NGC  7331", "UGC 12113", "Sh 2-162", "NAME Bubble Nebula"],
        }),
    )
    result = list_bright_object_choices("NGC 7331", write_stack(tmp_path / "stack.fits"), simbad)

    names = [entry.simbad_id for entry in result.objects]
    assert sorted(names) == sorted(["NGC 7331", "[ABB] big", "Sh 2-162"])


def test_the_best_common_name_wins_over_the_main_name() -> None:
    """Messier beats NGC, NGC beats Sh2, and an unknown catalog has no rank."""
    assert common_name_rank("M  52") == 0
    assert common_name_rank("NGC  7635") < common_name_rank("Sh 2-162")
    assert common_name_rank("[P98] 175") is None
    assert common_name_rank("NAME Bubble Nebula") is None


def test_the_bubble_gets_its_ngc_name(tmp_path: Path) -> None:
    """The main name is replaced by the best common identifier."""
    simbad = FakeSimbad(
        rows_with_ids((9, "NAME Bubble Nebula", CENTER_RA, CENTER_DEC, "HII", 15.0)),
        identifiers=Table({"oidref": [9, 9, 9], "id": ["Sh 2-162", "NGC  7635", "LBN 548"]}),
    )
    result = list_bright_object_choices("M 52", write_stack(tmp_path / "s.fits"), simbad)

    assert [entry.simbad_id for entry in result.objects] == ["NGC 7635"]


def test_a_failed_identifier_question_keeps_the_main_names(tmp_path: Path) -> None:
    """Without identifiers the sized objects still list by their main names."""
    simbad = FakeSimbad(rows_with_ids((9, "Some cloud", CENTER_RA, CENTER_DEC, "HII", 15.0)))
    result = list_bright_object_choices("M 52", write_stack(tmp_path / "s.fits"), simbad)

    assert [entry.simbad_id for entry in result.objects] == ["Some cloud"]
