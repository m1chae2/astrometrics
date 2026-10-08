"""Tests for the command-line script that downloads the deep-star catalog.

The script is what a person actually types, so these tests run it the same
way (through its entry point, with command-line arguments) and check what it
prints and the exit code it returns, against a fake archive that needs no
internet.
"""

import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import requests
from astropy.table import Table

from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.drivers.catalog import deep_star_catalog_builder
from wayfindinglib.scripts import build_deep_star_catalog as script


class _LibraryConfig:
    """A stand-in for `AppConfiguration` that points at a temporary folder."""

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path

    def get_library_path(self) -> Path:
        """Return the sandboxed library root.

        Returns
        -------
        path : `pathlib.Path`
            The temporary directory standing in for the library.
        """
        return self._library_path


class _FakeGaia:
    """A stand-in for ``astroquery.gaia.Gaia`` that invents stars."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def launch_job_async(self, query: str, **_keyword_arguments: Any) -> _FakeGaia._Job:
        """Record the query and return a job that answers it.

        Returns
        -------
        job : `_FakeGaia._Job`
            A job whose ``get_results`` gives the invented table.
        """
        self.queries.append(query)
        return _FakeGaia._Job(query)

    class _Job:
        def __init__(self, query: str) -> None:
            self._query = query

        def get_results(self) -> Table:
            """Invent a table for this job's query.

            Returns
            -------
            table : `astropy.table.Table`
                Ten stars, or a count of ten for a count query.
            """
            if "COUNT(*)" in self._query:
                return Table({"star_count": [10]})
            low = int(self._query.split("source_id >= ")[1].split(" ")[0])
            return Table({
                "source_id": low + np.arange(10, dtype=np.int64),
                "ra": np.linspace(100.0, 101.0, 10),
                "dec": np.linspace(10.0, 11.0, 10),
                "phot_g_mean_mag": np.linspace(8.0, 15.0, 10),
            })


@pytest.fixture
def fake_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeGaia:
    """Point the script at a temporary library and a fake archive.

    Returns
    -------
    gaia : `_FakeGaia`
        The fake archive, so a test can see what was asked of it.
    """
    gaia = _FakeGaia()
    fake_module = types.ModuleType("astroquery.gaia")
    fake_module.Gaia = gaia
    monkeypatch.setitem(sys.modules, "astroquery.gaia", fake_module)

    config = _LibraryConfig(tmp_path)
    # A real ObservationPlanning bound to the sandboxed config -- the deep-
    # catalog methods only ever touch self._butler.config, never the lazily
    # built Sky engine, so this exercises the real wiring end to end.
    fake_wayfinder = types.SimpleNamespace(
        config=config, planning=ObservationPlanning(butler=DiskButler(app_config=config))
    )
    monkeypatch.setattr(script, "Wayfinder", lambda: fake_wayfinder)
    monkeypatch.setattr(deep_star_catalog_builder.time, "sleep", lambda _seconds: None)
    return gaia


def test_dry_run_reports_the_plan_and_uses_no_internet(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run shows what would happen without contacting the archive."""
    exit_code = script.run_catalog_build(["--dry-run", "--healpix-level", "0"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "nothing downloaded yet" in output
    assert "Would download 12 more chunks" in output
    assert fake_environment.queries == []


def test_full_run_downloads_everything_and_says_it_is_complete(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clean run finishes, reports the counts, and exits with success."""
    exit_code = script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "12 of 12 chunks downloaded" in output
    assert "120 stars" in output
    assert "The catalog is complete. Restart Astrometrics to use it." in output
    assert len(fake_environment.queries) == 12


def test_a_short_trial_run_says_the_catalog_is_not_complete(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """A trial run of a few chunks tells the user how to carry on."""
    exit_code = script.run_catalog_build([
        "--healpix-level",
        "0",
        "--request-delay-seconds",
        "0",
        "--max-pixels",
        "3",
    ])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "3 of 12 chunks downloaded" in output
    assert "Run the same command again" in output


def test_running_again_resumes_instead_of_starting_over(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second run only fetches the chunks the first one did not."""
    script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0", "--max-pixels", "5"])
    queries_after_first_run = len(fake_environment.queries)

    exit_code = script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0"])

    assert exit_code == 0
    assert queries_after_first_run == 5
    assert len(fake_environment.queries) == 12  # only the 7 missing chunks were fetched
    assert "5 of 12 chunks downloaded" in capsys.readouterr().out


def test_different_settings_from_the_existing_catalog_are_refused(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resuming with another depth would mix incompatible chunks."""
    script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0", "--max-pixels", "1"])

    exit_code = script.run_catalog_build([
        "--healpix-level",
        "0",
        "--magnitude-limit",
        "15",
        "--request-delay-seconds",
        "0",
    ])

    assert exit_code == 2
    assert "Delete the file to start over" in capsys.readouterr().out


def test_estimate_prints_a_size_guess_and_saves_nothing(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The estimate counts a sample, prints a guess, and leaves no catalog."""
    exit_code = script.run_catalog_build([
        "--estimate",
        "--healpix-level",
        "0",
        "--request-delay-seconds",
        "0",
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Sampled 12 of 12 chunks" in output
    assert "estimated total: about 120 stars" in output
    assert "estimated disk" in output
    assert not list(tmp_path.rglob("deep_star_catalog.db"))


def test_ctrl_c_stops_cleanly_and_says_how_to_resume(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupting a long download is normal, not a crash."""

    def _interrupt(*_arguments: Any, **_keyword_arguments: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(fake_environment_planning(), "build_deep_star_catalog", _interrupt)

    exit_code = script.run_catalog_build(["--healpix-level", "0"])

    assert exit_code == 130
    assert "run the same command again to resume" in capsys.readouterr().out


def fake_environment_planning() -> ObservationPlanning:
    """Fetch the `ObservationPlanning` the `fake_environment` fixture built.

    Returns
    -------
    planning : `ObservationPlanning`
        The planning instance `script.Wayfinder()` will hand back.
    """
    return script.Wayfinder().planning


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(5, "5s"), (125, "2m"), (3 * 3600 + 7 * 60, "3h07m"), (0, "0s")],
)
def test_durations_are_written_the_way_a_person_would_say_them(seconds: int, expected: str) -> None:
    """The time-left estimate is easy to read at a glance."""
    assert script._format_duration(seconds) == expected


# A circle a bit bigger than one imaged field, in northern Cepheus.
NEAR_ARGUMENTS = ["--near", "315.13", "68.57", "1.5"]


def test_near_downloads_only_the_chunks_touching_the_circle(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """--near asks the archive for a few chunks, not the whole sky."""
    exit_code = script.run_catalog_build([
        "--healpix-level",
        "4",
        "--request-delay-seconds",
        "0",
        *NEAR_ARGUMENTS,
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert 1 <= len(fake_environment.queries) <= 4
    assert "touch the 1 chosen place(s)" in output
    assert "The chosen chunks are finished. Restart Astrometrics to use them." in output
    assert "The catalog is complete" not in output


def test_near_dry_run_counts_only_the_chosen_chunks_and_uses_no_internet(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run with --near says how many of the chosen chunks are missing."""
    exit_code = script.run_catalog_build(["--dry-run", "--healpix-level", "4", *NEAR_ARGUMENTS])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "more of the" in output
    assert "chosen chunks" in output
    assert "Would download 3,072" not in output
    assert fake_environment.queries == []


def test_a_second_near_run_adds_new_places_and_skips_saved_chunks(
    fake_environment: _FakeGaia, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running again with the same place downloads nothing new."""
    arguments = ["--healpix-level", "4", "--request-delay-seconds", "0", *NEAR_ARGUMENTS]
    script.run_catalog_build(arguments)
    first_run_query_count = len(fake_environment.queries)

    exit_code = script.run_catalog_build(arguments)

    assert exit_code == 0
    assert len(fake_environment.queries) == first_run_query_count


def test_near_targets_uses_the_fields_the_library_has_imaged(
    fake_environment: _FakeGaia, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--near-targets draws a circle around every imaged field."""
    fields = [
        {"right_ascension_deg": 315.13, "declination_deg": 68.57, "target_ids": ["NGC 7023"]},
        {"right_ascension_deg": 250.42, "declination_deg": 36.46, "target_ids": ["M 13"]},
    ]
    monkeypatch.setattr(fake_environment_planning(), "get_imaged_field_centers", lambda: fields)

    exit_code = script.run_catalog_build([
        "--healpix-level",
        "4",
        "--request-delay-seconds",
        "0",
        "--near-targets",
        "--near-targets-radius-degrees",
        "0.8",
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Found 2 imaged field(s)" in output
    assert "touch the 2 chosen place(s)" in output
    assert 2 <= len(fake_environment.queries) <= 8


def test_near_targets_with_no_imaged_fields_downloads_nothing(
    fake_environment: _FakeGaia, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty library gives a clear message, not a whole-sky download."""
    monkeypatch.setattr(fake_environment_planning(), "get_imaged_field_centers", lambda: [])

    exit_code = script.run_catalog_build(["--near-targets", "--healpix-level", "4"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "No imaged fields were found" in output
    assert fake_environment.queries == []


def test_near_and_near_targets_can_be_combined(
    fake_environment: _FakeGaia, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both kinds of place add up."""
    fields = [{"right_ascension_deg": 250.42, "declination_deg": 36.46, "target_ids": ["M 13"]}]
    monkeypatch.setattr(fake_environment_planning(), "get_imaged_field_centers", lambda: fields)

    script.run_catalog_build(["--dry-run", "--healpix-level", "4", "--near-targets", *NEAR_ARGUMENTS])

    assert "touch the 2 chosen place(s)" in capsys.readouterr().out


def test_a_failed_near_chunk_leaves_the_chosen_chunks_unfinished(
    fake_environment: _FakeGaia, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A chosen chunk that fails leaves a non-zero exit code and a hint."""

    def always_fail(*_arguments: Any, **_keyword_arguments: Any) -> Any:
        raise requests.exceptions.HTTPError("Error 500: archive down")

    monkeypatch.setattr(fake_environment, "launch_job_async", always_fail)

    exit_code = script.run_catalog_build([
        "--healpix-level",
        "4",
        "--request-delay-seconds",
        "0",
        "--max-attempts",
        "1",
        *NEAR_ARGUMENTS,
    ])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "The chosen chunks are not finished yet" in output
