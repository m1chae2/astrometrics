"""Guard tests for the star listing's target filter.

The filter in `CatalogAccess.query_star_summary_page` once wrapped the
``target_id`` column in text functions, so SQLite had to read all of the
roughly 290,000 star rows to answer it (about 0.9 s for one target). It now
uses an equality that the ``idx_stellar_objects_target_id`` index serves.
These tests build a small database with the real table layout and check two
things: the query plan never scans the whole table for a target, and the new
condition picks the same rows as the old one, including for stars whose
``target_id`` holds several comma-joined ids.
"""

import sqlite3
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib._internal.drivers.catalog_access import _TARGET_MEMBERSHIP_CONDITION, CatalogAccess
from astrometricslib.foundation.config import AppConfiguration

#: The whole-id-between-commas condition used before the index-friendly one.
OLD_CONDITION = (
    "instr(',' || replace(replace(replace(replace(COALESCE(target_id, ''), "
    "', ', ','), ' ,', ','), ', ', ','), ' ,', ',') || ',', ',' || ? || ',') > 0"
)

#: Stored ``target_id`` values: single ids, lists, odd spacing, empty, NULL.
STORED_TARGET_IDS: list[str | None] = [
    "M 81",
    "M 81",
    "M 8",
    "M 810",
    "M 13",
    "M 81,M 13",
    "M 13,M 81",
    "M 13, M 81",
    "M 13 ,M 81",
    "M 13 , M 81",
    "M 1,M 81,M 2",
    "Orion Field,M 13",
    "M 81 ",
    " M 81",
    "m 81",
    "",
    None,
    ",",
    "M 81,",
]
#: Ids to look up, including ones that appear only inside another id.
WANTED_TARGET_IDS = ["M 81", "M 13", "M 8", "M 1", "M 2", "Orion Field", "m 81", "Absent", "M 81,M 13", "81"]


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Build a database with the real star table and the varied target ids.

    Returns
    -------
    path : `pathlib.Path`
        The database file, created by the same code that creates the real
        one, so the indexes are the real ones.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    access = CatalogAccess(config)
    path = library_path / "astrometrics.db"
    # An empty file lets the first read create the table and its indexes.
    sqlite3.connect(path).close()
    access.query_star_summary_page(limit=1)
    with sqlite3.connect(path) as connection:
        for number, stored in enumerate(STORED_TARGET_IDS):
            connection.execute(
                "INSERT INTO stellar_objects (id, data_json, target_id, name, ra, dec, magnitude, "
                "has_spectra, has_photometry, spectral_type) VALUES (?, '{}', ?, ?, 0, 0, 5.0, 0, 0, '')",
                (f"HD {number:04d}", stored, f"Name {number}"),
            )
        # Enough other rows that a scan would be the planner's cheaper choice
        # only if the filter could not use the index.
        connection.executemany(
            "INSERT INTO stellar_objects (id, data_json, target_id, name, ra, dec, magnitude, "
            "has_spectra, has_photometry, spectral_type) VALUES (?, '{}', ?, ?, 0, 0, 5.0, 0, 0, '')",
            [
                (
                    f"BG {n:05d}",
                    f"Field {n % 50}",
                    f"Background {n}",
                )
                for n in range(3000)
            ],
        )
        connection.execute("ANALYZE")
    return path


def _ids_matching(path: Path, condition: str, parameters: list[str]) -> set[str]:
    """Run a target condition against the table.

    Returns
    -------
    ids : `set` [`str`]
        The ids of the stars the condition selects.
    """
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"SELECT id FROM stellar_objects WHERE {condition}",  # ruff: ignore[hardcoded-sql-expression]
            parameters,
        )
        return {row[0] for row in rows}


@pytest.mark.parametrize("wanted", WANTED_TARGET_IDS)
def test_new_condition_matches_the_old_one(database_path: Path, wanted: str) -> None:
    """The new condition selects exactly the rows the old one did."""
    old = _ids_matching(database_path, OLD_CONDITION, [wanted])
    new = _ids_matching(database_path, _TARGET_MEMBERSHIP_CONDITION, [wanted, wanted])
    assert new == old


def test_condition_finds_ids_inside_comma_lists(database_path: Path) -> None:
    """A target is found alone, in a list, and with spaces by a comma."""
    found = _ids_matching(database_path, _TARGET_MEMBERSHIP_CONDITION, ["M 81", "M 81"])
    # Rows 0 and 1 (single), 5 to 10 (lists, spacing) and 18 (trailing comma).
    assert found == {f"HD {number:04d}" for number in (0, 1, 5, 6, 7, 8, 9, 10, 18)}


@pytest.mark.parametrize("select_list", ["COUNT(*)", "id, name, ra, dec, target_id"])
def test_target_filter_does_not_scan_the_table(database_path: Path, select_list: str) -> None:
    """The count and page plans use an index, never a table scan."""
    sql = f"SELECT {select_list} FROM stellar_objects WHERE {_TARGET_MEMBERSHIP_CONDITION}"  # ruff: ignore[hardcoded-sql-expression]
    if select_list != "COUNT(*)":
        sql += " ORDER BY id LIMIT 100"
    with sqlite3.connect(database_path) as connection:
        plan = [row[3] for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", ["M 81", "M 81"])]
    scans_of_table = [
        detail for detail in plan if detail.startswith("SCAN stellar_objects") and "INDEX" not in detail
    ]
    assert not scans_of_table, plan
    assert any("idx_stellar_objects_target_id (target_id=?)" in detail for detail in plan), plan


def test_page_and_count_use_the_new_filter(database_path: Path, mocker: MockerFixture) -> None:
    """The public query returns the right stars and skips a needless count."""
    library_path = database_path.parent
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    access = CatalogAccess(config)
    spy = mocker.spy(access._generic, "count_where")

    total, page = access.query_star_summary_page(target_id="M 81", include_unresolved=True, limit=100)
    assert total == len(page) == 9
    assert spy.call_count == 0, "a page shorter than the limit already holds every match"

    total, page = access.query_star_summary_page(target_id="M 81", include_unresolved=True, limit=3)
    assert (total, len(page)) == (9, 3)
    assert spy.call_count == 1

    total, page = access.query_star_summary_page(target_id="M 81", include_unresolved=True, limit=5, offset=5)
    assert (total, len(page)) == (9, 4)
    assert spy.call_count == 2


def test_condition_still_works_on_a_database_without_the_index(database_path: Path) -> None:
    """The condition names no index, so a file without it gives one answer."""
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX idx_stellar_objects_target_id")
    found = _ids_matching(database_path, _TARGET_MEMBERSHIP_CONDITION, ["M 81", "M 81"])

    assert found == {f"HD {number:04d}" for number in (0, 1, 5, 6, 7, 8, 9, 10, 18)}
