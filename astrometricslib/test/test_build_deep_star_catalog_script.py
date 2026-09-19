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
from astropy.table import Table

from astrometricslib.pipelines.astrometry import deep_catalog_builder
from astrometricslib.scripts import build_deep_star_catalog as script


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
def fake_environment(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
    monkeypatch.setattr(script, "get_configuration", lambda: _LibraryConfig(tmp_path))
    monkeypatch.setattr(deep_catalog_builder.time, "sleep", lambda _seconds: None)
    return gaia


def test_dry_run_reports_the_plan_and_uses_no_internet(fake_environment, capsys):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A dry run shows what would happen without contacting the archive."""
    exit_code = script.run_catalog_build(["--dry-run", "--healpix-level", "0"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "nothing downloaded yet" in output
    assert "Would download 12 more chunks" in output
    assert fake_environment.queries == []


def test_full_run_downloads_everything_and_says_it_is_complete(fake_environment, capsys):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A clean run finishes, reports the counts, and exits with success."""
    exit_code = script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "12 of 12 chunks downloaded" in output
    assert "120 stars" in output
    assert "The catalog is complete. Restart Astrometrics to use it." in output
    assert len(fake_environment.queries) == 12


def test_a_short_trial_run_says_the_catalog_is_not_complete(fake_environment, capsys):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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


def test_running_again_resumes_instead_of_starting_over(fake_environment, capsys):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A second run only fetches the chunks the first one did not."""
    script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0", "--max-pixels", "5"])
    queries_after_first_run = len(fake_environment.queries)

    exit_code = script.run_catalog_build(["--healpix-level", "0", "--request-delay-seconds", "0"])

    assert exit_code == 0
    assert queries_after_first_run == 5
    assert len(fake_environment.queries) == 12  # only the 7 missing chunks were fetched
    assert "5 of 12 chunks downloaded" in capsys.readouterr().out


def test_different_settings_from_the_existing_catalog_are_refused(fake_environment, capsys):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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


def test_estimate_prints_a_size_guess_and_saves_nothing(fake_environment, capsys, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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


def test_ctrl_c_stops_cleanly_and_says_how_to_resume(fake_environment, capsys, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Interrupting a long download is normal, not a crash."""

    def _interrupt(*_arguments: Any, **_keyword_arguments: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(script, "build_deep_star_catalog", _interrupt)

    exit_code = script.run_catalog_build(["--healpix-level", "0"])

    assert exit_code == 130
    assert "run the same command again to resume" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(5, "5s"), (125, "2m"), (3 * 3600 + 7 * 60, "3h07m"), (0, "0s")],
)
def test_durations_are_written_the_way_a_person_would_say_them(seconds, expected):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The time-left estimate is easy to read at a glance."""
    assert script._format_duration(seconds) == expected
