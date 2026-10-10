"""Purpose: Unit tests for the script that moves stacks to the stacks path.

Description: The script finds the pipeline's output files (and only those),
copies them with a size check, and rewrites the old paths in the database and
the group manifests. These tests build a small library and database in a
temporary folder and check each step.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from astrometricslib.scripts.move_stacks_to_stacks_path import (
    copy_and_verify,
    count_remaining,
    is_stack_family_file,
    plan_moves,
    rewrite_database,
    rewrite_manifests,
    rewrite_paths,
)


def _library(tmp_path: Path) -> tuple[str, str]:
    """Build an old ``lights`` folder with raw frames and stacks.

    Returns
    -------
    old_lights, new_lights : `tuple` [`str`, `str`]
        The old ``lights`` folder and the one the files should move to.
    """
    target = tmp_path / "frames" / "lights" / "M 27"
    (target / "Apertura 75Q" / "ZWO ASI 533MM Pro").mkdir(parents=True)
    (target / "groups").mkdir()
    (target / "Apertura 75Q" / "ZWO ASI 533MM Pro" / "M_27_Light_001.fits").write_bytes(b"raw")
    (target / "M_27_L_Stacked_ZWO.fits").write_bytes(b"stack")
    (target / "M_27_L_Stacked_ZWO_RejMap.fits").write_bytes(b"rej")
    (target / "M_27_L_Stacked_ZWO_preview.jpg").write_bytes(b"jpg")
    (target / "M_27_L_Stacked_ZWO_processed.fits").write_bytes(b"proc")
    (target / "starless_M_27_Stacked.fits").write_bytes(b"sl")
    (target / "M 27.jpg").write_bytes(b"my picture")
    (target / "groups" / "M_27_exp60s.fits").write_bytes(b"group")
    return str(tmp_path / "frames" / "lights"), str(tmp_path / "stacks" / "lights")


def test_stack_family_names_are_recognised() -> None:
    """Stacks and their companions match; raw frames and pictures do not."""
    assert is_stack_family_file("M_27_L_Stacked_ZWO.fits")
    assert is_stack_family_file("M_27_L_Stacked_ZWO_RejMap.fits")
    assert is_stack_family_file("M_27_Stacked_Registration.seq")
    assert is_stack_family_file("M_27_Stacked_preview.jpg")
    assert is_stack_family_file("M_27_Stacked_processed.fits")
    assert is_stack_family_file("starmask_M_27_Stacked.fits")
    assert not is_stack_family_file("M_27_Light_001.fits")
    assert not is_stack_family_file("M 27.jpg")


def test_the_plan_moves_stacks_and_groups_but_not_raw_frames_or_pictures(tmp_path: Path) -> None:
    """Only the pipeline's output is planned, keeping its layout."""
    old_lights, new_lights = _library(tmp_path)

    moves = plan_moves(old_lights, new_lights)

    names = sorted(Path(old).name for old in moves)
    assert names == [
        "M_27_L_Stacked_ZWO.fits",
        "M_27_L_Stacked_ZWO_RejMap.fits",
        "M_27_L_Stacked_ZWO_preview.jpg",
        "M_27_L_Stacked_ZWO_processed.fits",
        "M_27_exp60s.fits",
        "starless_M_27_Stacked.fits",
    ]
    assert moves[f"{old_lights}/M 27/groups/M_27_exp60s.fits"] == f"{new_lights}/M 27/groups/M_27_exp60s.fits"


def test_copy_and_verify_copies_and_skips_a_complete_copy(tmp_path: Path) -> None:
    """A file is copied once and a rerun accepts the finished copy."""
    source = tmp_path / "a.fits"
    source.write_bytes(b"12345")
    destination = tmp_path / "other" / "a.fits"

    assert copy_and_verify(str(source), str(destination))
    assert destination.read_bytes() == b"12345"
    destination.write_bytes(b"12345")  # same size: left alone
    assert copy_and_verify(str(source), str(destination))


def test_rewrite_paths_replaces_whole_paths_only() -> None:
    """Only paths in the mapping change, and the longer path wins."""
    mapping = {"/a/lights/M 27/s.fits": "/b/lights/M 27/s.fits", "/a/lights/M 27/s.fits.bak": "/b/x.bak"}
    text = json.dumps({
        "stack": "/a/lights/M 27/s.fits",
        "raw": "/a/lights/M 27/raw/1.fits",
        "old": "/a/lights/M 27/s.fits.bak",
    })

    result = json.loads(rewrite_paths(text, mapping))

    assert result == {"stack": "/b/lights/M 27/s.fits", "raw": "/a/lights/M 27/raw/1.fits", "old": "/b/x.bak"}


def test_rewrite_database_updates_json_and_plain_columns(tmp_path: Path) -> None:
    """A JSON blob and a plain location column are both rewritten."""
    database = tmp_path / "test.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE targets (id TEXT, data_json TEXT)")
    connection.execute("CREATE TABLE prov_entity (id TEXT, location TEXT)")
    connection.execute("INSERT INTO targets VALUES ('M 27', ?)", ('{"s": "/a/lights/M 27/s.fits", "n": 3}',))
    connection.execute("INSERT INTO prov_entity VALUES ('e1', '/a/lights/M 27/s.fits')")
    connection.execute("INSERT INTO prov_entity VALUES ('e2', '/a/lights/M 27/raw/1.fits')")
    connection.commit()
    connection.close()
    moves = {"/a/lights/M 27/s.fits": "/b/lights/M 27/s.fits"}

    assert rewrite_database(str(database), "targets", "data_json", moves) == 1
    assert rewrite_database(str(database), "prov_entity", "location", moves) == 1

    connection = sqlite3.connect(database)
    assert (
        json.loads(connection.execute("SELECT data_json FROM targets").fetchone()[0])["s"]
        == "/b/lights/M 27/s.fits"
    )
    locations = dict(connection.execute("SELECT id, location FROM prov_entity").fetchall())
    assert locations == {"e1": "/b/lights/M 27/s.fits", "e2": "/a/lights/M 27/raw/1.fits"}
    connection.close()


def test_group_manifests_point_at_the_new_group_stacks(tmp_path: Path) -> None:
    """A moved manifest is rewritten and stays valid JSON."""
    new_manifest = tmp_path / "new" / "M_27_manifest.json"
    new_manifest.parent.mkdir()
    new_manifest.write_text(json.dumps({"groups": [{"path": "/old/lights/M 27/groups/g60.fits"}]}))
    moves = {
        "/old/lights/M 27/groups/g60.fits": "/new/lights/M 27/groups/g60.fits",
        "/old/lights/M 27/groups/M_27_manifest.json": str(new_manifest),
    }

    assert rewrite_manifests(moves) == 1
    assert json.loads(new_manifest.read_text())["groups"][0]["path"] == "/new/lights/M 27/groups/g60.fits"


@pytest.mark.parametrize("column", ["data_json"])
def test_rewrite_database_leaves_unrelated_rows_alone(tmp_path: Path, column: str) -> None:
    """A row with no moved path is not touched."""
    database = tmp_path / "test.db"
    connection = sqlite3.connect(database)
    connection.execute(f"CREATE TABLE targets (id TEXT, {column} TEXT)")
    connection.execute("INSERT INTO targets VALUES ('M 1', '{\"s\": \"/a/lights/M 1/other.fits\"}')")
    connection.commit()
    connection.close()

    assert rewrite_database(str(database), "targets", column, {"/a/lights/M 27/s.fits": "/b/s.fits"}) == 0


def test_count_remaining_finds_rows_that_still_hold_an_old_path(tmp_path: Path) -> None:
    """The check reports a row the rewrite missed and none once it is fixed."""
    database = tmp_path / "test.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE targets (id TEXT, data_json TEXT)")
    connection.execute("INSERT INTO targets VALUES ('M 27', '{\"s\": \"/a/lights/M 27/s.fits\"}')")
    connection.commit()
    connection.close()
    moves = {"/a/lights/M 27/s.fits": "/b/lights/M 27/s.fits"}

    assert count_remaining(str(database), "targets", "data_json", moves) == 1
    rewrite_database(str(database), "targets", "data_json", moves)
    assert count_remaining(str(database), "targets", "data_json", moves) == 0
