"""Tests for the Gaia star colors of a piece of sky.

The Gaia archive is replaced by a stand-in, so no test touches the network.
Stored answers go to a folder the test owns.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.catalog_cache import CatalogUnavailableError
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.shared import gaia_star_colors
from astrometricslib.pipelines.shared.gaia_star_colors import COLOUR_COLUMNS, gaia_star_colors_in_footprint


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own stored catalog answers.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's own folder.
    monkeypatch : `pytest.MonkeyPatch`
        Used to move the cache folder.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


# A footprint of about 0.1 degree on a side, inside one tile.
FOOTPRINT = [(10.0, 20.0), (10.1, 20.0), (10.1, 20.1), (10.0, 20.1)]


def archive_rows(count: int = 3) -> Table:
    """Make the table the Gaia archive would answer with.

    Parameters
    ----------
    count : `int`, optional
        How many stars.

    Returns
    -------
    rows : `astropy.table.Table`
        Columns as the archive names them. The second star has no color.
    """
    bp_rp = np.ma.masked_array(np.linspace(0.8, 1.2, count), mask=[False, True, *[False] * (count - 2)])
    return Table({
        "source_id": np.arange(1000, 1000 + count, dtype=np.int64),
        "ra": np.linspace(10.0, 10.1, count),
        "dec": np.linspace(20.0, 20.1, count),
        "phot_g_mean_mag": np.linspace(11.0, 14.0, count),
        "bp_rp": bp_rp,
    })


def install_archive(monkeypatch: pytest.MonkeyPatch, answer: Table | Exception) -> list[str]:
    """Replace the archive query with a stand-in and record the questions.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the query.
    answer : `astropy.table.Table` or `Exception`
        What every question gets: rows, or an error to raise.

    Returns
    -------
    asked : `list` [`str`]
        Receives each question's text.
    """
    asked: list[str] = []

    def query(adql_query: str, *, asynchronous: bool = False) -> Table:
        """Answer one question.

        Returns
        -------
        rows : `astropy.table.Table`
            The answer.
        """
        asked.append(adql_query)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(outside_services, "gaia_archive_query", query)
    return asked


def test_the_stars_come_back_with_their_colors_and_nan_for_a_missing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The table has the documented columns and a missing color is NaN."""
    install_archive(monkeypatch, archive_rows())

    stars = gaia_star_colors_in_footprint(FOOTPRINT, 0.01)

    assert stars is not None
    assert tuple(stars.colnames) == COLOUR_COLUMNS
    assert np.isnan(stars["bp_rp"]).sum() == 1
    assert sorted(stars["source_id"]) == [1000, 1001, 1002]
    assert stars["g"].min() >= 10.5


def test_the_question_asks_for_the_magnitude_range_and_the_source_id_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADQL names the color column, a source ID range and the G limits."""
    asked = install_archive(monkeypatch, archive_rows())

    gaia_star_colors_in_footprint(FOOTPRINT, 0.01)

    assert "bp_rp" in asked[0]
    assert "source_id >=" in asked[0]
    assert "source_id <" in asked[0]
    brightest, faintest = gaia_star_colors.GAIA_COLOUR_MAGNITUDE_RANGE.default
    assert f"phot_g_mean_mag < {faintest}" in asked[0]
    assert f"phot_g_mean_mag >= {brightest}" in asked[0]


def test_a_second_question_for_the_same_sky_uses_the_stored_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The archive is asked once; the next call reads the disk."""
    asked = install_archive(monkeypatch, archive_rows())
    gaia_star_colors_in_footprint(FOOTPRINT, 0.01)
    first_count = len(asked)

    stars = gaia_star_colors_in_footprint(FOOTPRINT, 0.01)

    assert len(asked) == first_count
    assert stars is not None
    assert len(stars) == 3


def test_stored_answers_alone_work_without_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """With live questions off, a sky never asked for is unavailable."""
    install_archive(monkeypatch, archive_rows())

    with pytest.raises(CatalogUnavailableError):
        gaia_star_colors_in_footprint(FOOTPRINT, 0.01, allow_live=False)


def test_an_unreachable_archive_raises_the_catalog_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed question with nothing stored is an `ExternalServiceError`."""
    install_archive(monkeypatch, ExternalServiceError("no answer"))

    with pytest.raises(ExternalServiceError):
        gaia_star_colors_in_footprint(FOOTPRINT, 0.01)


def test_an_answer_at_the_row_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An answer cut off at the row limit may lack stars, so it is not used."""
    monkeypatch.setattr(gaia_star_colors, "_ROW_LIMIT", 3)
    install_archive(monkeypatch, archive_rows(count=3))

    with pytest.raises(ExternalServiceError):
        gaia_star_colors_in_footprint(FOOTPRINT, 0.01)
