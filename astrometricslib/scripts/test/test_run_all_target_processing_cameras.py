"""Tests for how the batch script picks its camera passes.

The script used to have two cameras written into it. It now takes them from
the config's setups. These tests use stand-in objects, so they never touch the
real library, the real config, or Siril.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.foundation.config import _TomlSectionedConfig
from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig

_EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "astrometrics.config.example.toml"


def _shipped_camera_sections() -> _TomlSectionedConfig:
    """Parse the real camera sections out of the shipped config template.

    Returns
    -------
    app_config : `_TomlSectionedConfig`
        A config carrying the shipped cameras, so name resolution in
        `camera_profile_store` has real profiles to match against.
    """
    parser = _TomlSectionedConfig()
    parser.read_string(_EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    return parser


SETUPS = ObservatorySetups(
    optics=(OpticConfig(name="Apertura 75Q", focal_length_mm=405.0),),
    setups=(
        SetupConfig(name="ASI533 on Apertura", camera_name="ZWO ASI 533MM Pro", optic_name="Apertura 75Q"),
        SetupConfig(name="D5300 on Apertura", camera_name="Nikon D5300", optic_name="Apertura 75Q"),
    ),
)
ASI = "ZWO ASI 533MM Pro"
NIKON = "Nikon DSLR DSC D5300"


def make_target(target_id: str, *cameras: str) -> SimpleNamespace:
    """Build a stand-in target with one frame per camera given.

    Returns
    -------
    target : `types.SimpleNamespace`
        A target with an id and frames.
    """
    return SimpleNamespace(
        id=target_id,
        frames=[
            SimpleNamespace(camera=camera, path=f"{target_id}/light_{index}.fit", filter="L")
            for index, camera in enumerate(cameras)
        ],
    )


class FakeConfig:
    """A hashable stand-in for `AppConfiguration`.

    `camera_profile_store.load_camera_profiles` caches on its `config`
    argument with `functools.cache`, which requires a hashable key --
    `types.SimpleNamespace` isn't one, so this uses a plain object instead.
    """

    def __init__(self, setups: ObservatorySetups) -> None:
        """Store the setups this fake config should report.

        Parameters
        ----------
        setups : `ObservatorySetups`
            The setups the config describes.
        """
        self._setups = setups
        self.app_config = _shipped_camera_sections()

    def get_observatory_setups(self) -> ObservatorySetups:
        """Return the setups given at construction.

        Returns
        -------
        setups : `ObservatorySetups`
            The setups the config describes.
        """
        return self._setups

    def get_primary_camera_name(self) -> str:
        """Return the stand-in primary camera name.

        Returns
        -------
        name : `str`
            The fixed primary camera name used by these tests.
        """
        return ASI

    def get_primary_focal_length_mm(self) -> float | None:
        """Return the stand-in primary focal length.

        Returns
        -------
        focal_length_mm : `None`
            Always `None`; these tests don't exercise focal length.
        """
        return None


class FakeAstrometrics:
    """A stand-in for `Astrometrics` that records the passes asked for."""

    def __init__(self, targets: list[SimpleNamespace], setups: ObservatorySetups) -> None:
        """Set up the fake.

        Parameters
        ----------
        targets : `list`
            The targets the catalog holds.
        setups : `ObservatorySetups`
            The setups the config describes.
        """
        self.targets = SimpleNamespace(
            list=lambda: targets,
            reindex_frames=lambda *args, **kwargs: None,
            save=lambda: None,
        )
        self.config = FakeConfig(setups)
        self.calls: list[dict[str, Any]] = []
        self.stack_calls: list[dict[str, Any]] = []
        self.stack_failures: set[str] = set()
        self.process_failures: set[str] = set()
        self.quality_rows: dict[str, list[SimpleNamespace]] = {}
        self.processing = SimpleNamespace(process_target=self.process_target, stack=self.stack)
        self.quality = SimpleNamespace(query=self.query_quality)

    def stack(self, target: Any, **keywords: Any) -> SimpleNamespace:
        """Record one stack request, or fail it when the test says so.

        Returns
        -------
        result : `types.SimpleNamespace`
            An empty stack result.

        Raises
        ------
        RuntimeError
            If the target is in `stack_failures`.
        """
        self.stack_calls.append({"target": target.id, **keywords})
        if target.id in self.stack_failures:
            raise RuntimeError(f"stacking {target.id} broke")
        return SimpleNamespace()

    def query_quality(self, metric: str, target_id: str, **keywords: Any) -> list[SimpleNamespace]:
        """Return the fake quality rows of one metric for one target.

        Returns
        -------
        rows : `list`
            The rows the test set up, or none.
        """
        return self.quality_rows.get(f"{target_id}|{metric}", [])

    def process_target(self, target: list[str] | None, **keywords: Any) -> SimpleNamespace:
        """Record one pass and pretend it succeeded.

        Returns
        -------
        summary : `types.SimpleNamespace`
            An empty pass summary.
        """
        self.calls.append({"target": target, **keywords})
        report = keywords.get("on_item_complete")
        if report is not None:
            for position, target_id in enumerate(target, start=1):
                failed = target_id in self.process_failures
                result = {"status": "failed" if failed else "success", "error": "boom" if failed else None}
                report(target_id, result, position, len(target))
        return SimpleNamespace(results={}, succeeded=[], skipped=[], failed=[])


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import the script with the stale-work-folder cleanup switched off.

    Returns
    -------
    module : `module`
        The imported script module.
    """
    monkeypatch.setenv("HEADLESS", "0")
    from astrometricslib.drivers import siril_interface
    from astrometricslib.scripts import run_all_target_processing

    monkeypatch.setattr(siril_interface, "purge_stale_work_directories", lambda *args, **kwargs: (0, 0))
    return run_all_target_processing


TARGETS = [
    make_target("Both", ASI, NIKON),
    make_target("AsiOnly", ASI),
    make_target("NikonOnly", NIKON),
    make_target("Neither", "Some Other Camera"),
]


def test_a_dry_run_lists_each_camera_pass_from_the_configs_setups(
    script: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check the preview shows the same two passes the old script had."""
    fake = FakeAstrometrics(TARGETS, SETUPS)
    monkeypatch.setattr(script, "Astrometrics", lambda: fake)

    script.run_full_processing(["--dry-run"])

    output = capsys.readouterr().out
    assert f"Pass 1 ({ASI}) would process 2: Both, AsiOnly" in output
    assert f"Pass 2 ({NIKON}) would process 1: NikonOnly" in output
    assert "No frames for any configured camera (1): Neither" in output
    assert fake.calls == []


def test_a_real_run_processes_each_camera_with_its_own_targets(
    script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check that each pass gets the full camera name and only its targets.

    The shipped Nikon profile has no ISO 800 noise entry yet, so its pass is
    refused; the ZWO pass runs regardless.
    """
    fake = FakeAstrometrics(TARGETS, SETUPS)
    monkeypatch.setattr(script, "Astrometrics", lambda: fake)

    script.run_full_processing(["--skip-reindex"])

    assert [(call["camera_id"], call["target"]) for call in fake.calls] == [(ASI, ["Both", "AsiOnly"])]


def test_a_config_without_setups_stops_with_a_clear_message(
    script: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check that an older config is told what to add, and nothing is run."""
    fake = FakeAstrometrics(TARGETS, ObservatorySetups())
    monkeypatch.setattr(script, "Astrometrics", lambda: fake)

    script.run_full_processing(["--skip-reindex"])

    assert "[Observatory.Setups]" in capsys.readouterr().out
    assert fake.calls == []


# -- Forced-restack options ---------------------------------------------------


@pytest.fixture
def fake(script: Any, monkeypatch: pytest.MonkeyPatch) -> FakeAstrometrics:
    """Install a fake library that holds the standard four targets.

    Returns
    -------
    fake : `FakeAstrometrics`
        The installed fake.
    """
    installed = FakeAstrometrics(TARGETS, SETUPS)
    monkeypatch.setattr(script, "Astrometrics", lambda: installed)
    return installed


def read_lines(path: Path) -> list[dict]:
    """Read a progress file as a list of records.

    Returns
    -------
    records : `list` [`dict`]
        One record per line.
    """
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_stack_only_stacks_forced_and_runs_no_other_step(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --stack-only makes only forced stack calls."""
    progress_path = tmp_path / "progress.jsonl"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--force-restack", "--camera", ASI,
        "--progress-file", str(progress_path),
    ])  # fmt: skip

    assert fake.calls == []
    assert [(call["target"], call["force"], len(call["frames"])) for call in fake.stack_calls] == [
        ("Both", True, 1),
        ("AsiOnly", True, 1),
    ]
    assert [(record["target"], record["status"]) for record in read_lines(progress_path)] == [
        ("Both", "success"),
        ("AsiOnly", "success"),
    ]


def test_stack_only_records_a_failure_and_goes_on(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check a failed stack is recorded and the next target runs."""
    fake.stack_failures = {"Both"}
    progress_path = tmp_path / "progress.jsonl"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--camera", ASI, "--progress-file", str(progress_path),
    ])  # fmt: skip

    records = read_lines(progress_path)
    assert [(record["target"], record["status"]) for record in records] == [
        ("Both", "failed"),
        ("AsiOnly", "success"),
    ]
    assert "stacking Both broke" in records[0]["error"]
    assert all(call["force"] is False for call in fake.stack_calls)


def test_resume_skips_done_targets_and_retries_failed_ones_only_when_asked(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check resume skips done targets; --retry-failed reruns failures."""
    progress_path = tmp_path / "progress.jsonl"
    fake.stack_failures = {"AsiOnly"}
    base = ["--skip-reindex", "--stack-only", "--camera", ASI, "--progress-file", str(progress_path)]
    script.run_full_processing(base)
    fake.stack_calls.clear()
    fake.stack_failures = set()

    script.run_full_processing(base)
    assert fake.stack_calls == []

    script.run_full_processing([*base, "--retry-failed"])
    assert [call["target"] for call in fake.stack_calls] == ["AsiOnly"]
    assert read_lines(progress_path)[-1]["status"] == "success"


def test_resume_works_for_the_full_pipeline_too(script: Any, fake: FakeAstrometrics, tmp_path: Path) -> None:
    """Check the full pipeline writes progress lines and resumes."""
    progress_path = tmp_path / "progress.jsonl"
    fake.process_failures = {"AsiOnly"}
    base = ["--skip-reindex", "--camera", ASI, "--progress-file", str(progress_path)]

    script.run_full_processing(base)
    script.run_full_processing(base)
    script.run_full_processing([*base, "--retry-failed"])

    assert [call["target"] for call in fake.calls] == [["Both", "AsiOnly"], ["AsiOnly"]]


def test_a_broken_last_progress_line_is_skipped_with_a_warning(
    script: Any, fake: FakeAstrometrics, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check a line cut short by a kill is skipped and appending works."""
    progress_path = tmp_path / "progress.jsonl"
    good = {"target": "Both", "camera": ASI, "optic_mm": None, "status": "success", "error": ""}
    progress_path.write_text(json.dumps(good) + '\n{"target": "AsiOn')

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--camera", ASI, "--progress-file", str(progress_path),
    ])  # fmt: skip

    assert "WARNING: skipping an unreadable line 2" in capsys.readouterr().out
    assert [call["target"] for call in fake.stack_calls] == ["AsiOnly"]
    lines = progress_path.read_text().splitlines()
    assert json.loads(lines[-1])["target"] == "AsiOnly"


def test_the_camera_filter_keeps_the_earlier_cameras_targets_with_that_camera(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --camera runs only that pass; part of a name is enough."""
    script.run_full_processing(["--skip-reindex", "--stack-only", "--camera", "zwo"])

    assert [call["target"] for call in fake.stack_calls] == ["Both", "AsiOnly"]


def test_an_unknown_camera_stops_the_run(
    script: Any, fake: FakeAstrometrics, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check a --camera that matches nothing runs nothing."""
    script.run_full_processing(["--skip-reindex", "--stack-only", "--camera", "Atik"])

    assert "No camera matches" in capsys.readouterr().out
    assert fake.stack_calls == []


def test_a_nikon_target_is_refused_with_its_reason_while_a_zwo_target_runs(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check the D5300 is refused with a reason while ZWO runs."""
    progress_path = tmp_path / "progress.jsonl"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--progress-file", str(progress_path),
    ])  # fmt: skip

    assert [call["target"] for call in fake.stack_calls] == ["Both", "AsiOnly"]
    by_target = {record["target"]: record for record in read_lines(progress_path)}
    assert by_target["NikonOnly"]["status"] == "refused"
    assert "no sensor-noise entry for ISO 800, offset 0, binning 1" in by_target["NikonOnly"]["error"]
    assert "above the clip ceiling" in by_target["NikonOnly"]["error"]
    assert by_target["AsiOnly"]["status"] == "success"


def test_a_nikon_target_with_a_complete_profile_is_not_refused(
    script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check the guard passes with the entry and a reachable level."""
    complete_profile = SimpleNamespace(
        camera_name="Nikon D5300",
        sensor_noise_for=lambda *setting: SimpleNamespace() if setting == ("800", 0, 1) else None,
        saturation_threshold_can_be_reached=True,
    )
    monkeypatch.setattr(script, "resolve_camera_profile", lambda *arguments: complete_profile)

    assert script.camera_refusal_reason(NIKON, FakeConfig(SETUPS)) is None


def test_the_shipped_profiles_give_the_expected_guard_answers(script: Any) -> None:
    """Check today's D5300 profile is refused and the ZWO profile is not."""
    config = FakeConfig(SETUPS)

    assert script.camera_refusal_reason(NIKON, config) is not None
    assert script.camera_refusal_reason(ASI, config) is None


def rows_for(*values: Any, status: str = "measured") -> list[SimpleNamespace]:
    """Build fake quality rows holding the given values.

    Returns
    -------
    rows : `list` [`types.SimpleNamespace`]
        One row per value.
    """
    return [SimpleNamespace(value=value, status=status, action_taken=None) for value in values]


def test_the_review_list_has_one_row_per_target_with_what_was_recorded(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check the CSV rows hold what the run recorded."""
    from astrometricslib.pipelines.stacking.metrics import (
        FRAME_QUARANTINE_ROUNDNESS,
        STACKING_APERTURE_SET_NUMBER,
        STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
        STACKING_FRAME_WEIGHT,
    )

    moved = SimpleNamespace(
        value=0.4,
        status="measured",
        action_taken="frame moved to _excluded/: cloud (sky too bright)",
        subject=SimpleNamespace(subject_id="Both/light_0.fit"),
    )
    kept = SimpleNamespace(
        value=0.9,
        status="measured",
        action_taken=None,
        subject=SimpleNamespace(subject_id="Both/light_1.fit"),
    )
    fake.quality_rows = {
        f"Both|{STACKING_FRAME_WEIGHT}": rows_for(1.0, 0.8),
        f"Both|{STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT}": rows_for(2.0e-3),
        f"Both|{STACKING_APERTURE_SET_NUMBER}": rows_for(3.0),
        f"Both|{FRAME_QUARANTINE_ROUNDNESS}": [moved, kept],
        f"AsiOnly|{STACKING_FRAME_WEIGHT}": rows_for(1.0, status="assumed"),
    }
    progress_path = tmp_path / "progress.jsonl"
    review_path = tmp_path / "review.csv"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--progress-file", str(progress_path),
        "--review-list", str(review_path),
    ])  # fmt: skip

    import csv

    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    assert list(rows) == ["AsiOnly", "Both", "NikonOnly"]
    assert rows["Both"]["weights_known"] == "yes"
    assert rows["Both"]["predicted_noise_present"] == "yes"
    assert rows["Both"]["aperture_set_present"] == "yes"
    assert rows["Both"]["frames_moved_count"] == "1"
    assert rows["Both"]["frames_moved"] == "light_0.fit"
    assert rows["AsiOnly"]["weights_known"] == "assumed"
    assert rows["AsiOnly"]["predicted_noise_present"] == "no"
    assert rows["NikonOnly"]["status"] == "refused"
    assert "no sensor-noise entry" in rows["NikonOnly"]["refusal_reason"]
    assert rows["NikonOnly"]["weights_known"] == "n/a"


def test_review_only_reads_the_progress_file_and_processes_nothing(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --review-only writes the list and stacks nothing."""
    progress_path = tmp_path / "progress.jsonl"
    record = {
        "target": "Both",
        "camera": ASI,
        "optic_mm": 405.0,
        "status": "success",
        "error": "",
        "time": datetime.now(UTC).isoformat(),
        "run_started_at": "2026-10-27T00:00:00+00:00",
    }
    progress_path.write_text(json.dumps(record) + "\n")
    review_path = tmp_path / "review.csv"

    script.run_full_processing([
        "--review-only", "--progress-file", str(progress_path), "--review-list", str(review_path),
    ])  # fmt: skip

    assert fake.stack_calls == [] and fake.calls == []
    text = review_path.read_text()
    assert text.splitlines()[0].startswith("target,camera,optic_mm,status")
    assert "Both" in text and "405" in text


def test_a_dry_run_with_the_new_options_writes_nothing(
    script: Any, fake: FakeAstrometrics, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check a dry run lists targets and refusals but writes no file."""
    progress_path = tmp_path / "progress.jsonl"
    review_path = tmp_path / "review.csv"

    script.run_full_processing([
        "--dry-run", "--stack-only", "--force-restack", "--progress-file", str(progress_path),
        "--review-list", str(review_path),
    ])  # fmt: skip

    output = capsys.readouterr().out
    assert "Both: 1 imaging frame(s) from this camera" in output
    assert "REFUSED: " in output
    assert fake.stack_calls == [] and fake.calls == []
    assert list(tmp_path.iterdir()) == []
