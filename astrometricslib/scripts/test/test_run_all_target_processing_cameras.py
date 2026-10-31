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

from astrometricslib import EARLIER_NIGHTS_VARIANT
from astrometricslib.foundation.config import _TomlSectionedConfig
from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_FRAME_WEIGHT,
    STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO,
    STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT,
)

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
        self.measure_calls: list[dict[str, Any]] = []
        self.measuring_elsewhere: set[str] = set()
        self.earlier_nights_calls: list[dict[str, Any]] = []
        self.earlier_nights_failures: set[str] = set()
        self.odd_even_difference_calls: list[str] = []
        self.odd_even_difference_failures: set[str] = set()
        self.processing = SimpleNamespace(
            measure_odd_even_difference_noise=self.measure_odd_even_difference_noise,
            process_target=self.process_target,
            stack=self.stack,
            stack_subset=self.stack_subset,
            record_frame_measurements=self.record_frame_measurements,
        )
        self.quality = SimpleNamespace(query=self.query_quality)

    def record_frame_measurements(self, target: Any, **keywords: Any) -> SimpleNamespace:
        """Record one frame-measurement request.

        Returns
        -------
        summary : `types.SimpleNamespace`
            A summary that measured every frame it was given, or one that says
            another run has the turn when the test says so.
        """
        self.measure_calls.append({"target": target.id, **keywords})
        frame_count = len(keywords.get("frames") or [])
        return SimpleNamespace(
            already_running=target.id in self.measuring_elsewhere,
            frames_measured=frame_count,
            frames_skipped_unchanged=0,
            frames_cut_short=0,
            frames_failed=0,
        )

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

    def stack_subset(self, target: Any, frames: list[Any], **keywords: Any) -> SimpleNamespace:
        """Record one subset request, or fail it when the test says so.

        Returns
        -------
        result : `types.SimpleNamespace`
            An empty subset result.

        Raises
        ------
        RuntimeError
            If the target is in `earlier_nights_failures`.
        """
        self.earlier_nights_calls.append({"target": target.id, "frames": frames, **keywords})
        if target.id in self.earlier_nights_failures:
            raise RuntimeError(f"the earlier nights of {target.id} broke")
        return SimpleNamespace()

    def measure_odd_even_difference_noise(self, target: Any, **keywords: Any) -> SimpleNamespace:
        """Record one odd–even difference request, or fail it on request.

        Returns
        -------
        result : `types.SimpleNamespace`
            An empty result.

        Raises
        ------
        RuntimeError
            If the target is in `odd_even_difference_failures`.
        """
        self.odd_even_difference_calls.append(target.id)
        if target.id in self.odd_even_difference_failures:
            raise RuntimeError(f"the odd–even difference of {target.id} broke")
        return SimpleNamespace()

    def query_quality(
        self, metric: str, target_id: str, variant: str | None = None, **keywords: Any
    ) -> list[SimpleNamespace]:
        """Return the fake quality rows of one metric for one target.

        Rows asked for by a subset variant are kept under the key with the
        variant added.

        Returns
        -------
        rows : `list`
            The rows the test set up, or none.
        """
        key = f"{target_id}|{metric}" + (f"|{variant}" if variant else "")
        return self.quality_rows.get(key, [])

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


def test_stack_only_measures_every_imaging_frame_before_it_stacks(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --stack-only measures all imaging frames before stacking."""
    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--force-restack", "--camera", ASI,
        "--progress-file", str(tmp_path / "progress.jsonl"),
    ])  # fmt: skip

    assert [(call["target"], len(call["frames"]), call.get("limit")) for call in fake.measure_calls] == [
        ("Both", 1, None),
        ("AsiOnly", 1, None),
    ]
    assert [call["frames"] for call in fake.measure_calls] == [call["frames"] for call in fake.stack_calls]


def test_stack_only_does_not_stack_while_another_run_measures_the_frames(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check a target being measured elsewhere fails and is not stacked."""
    fake.measuring_elsewhere = {"Both"}
    progress_path = tmp_path / "progress.jsonl"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--camera", ASI, "--progress-file", str(progress_path),
    ])  # fmt: skip

    records = read_lines(progress_path)
    assert [(record["target"], record["status"]) for record in records] == [
        ("Both", "failed"),
        ("AsiOnly", "success"),
    ]
    assert "another run is measuring" in records[0]["error"]
    assert [call["target"] for call in fake.stack_calls] == ["AsiOnly"]


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


def rows_for(
    *values: Any,
    status: str = "measured",
    nights: tuple[str | None, ...] = (),
    subject_ids: tuple[str, ...] = (),
    reason: str = "",
) -> list[SimpleNamespace]:
    """Build fake quality rows holding the given values.

    Parameters
    ----------
    *values : `Any`
        One value for each row.
    status : `str`, optional
        The rows' status.
    nights : `tuple` [`str` or `None`, ...], optional
        The observing night of each row's subject, in the order of the values;
        a row past the end of the list has none.
    subject_ids : `tuple` [`str`, ...], optional
        The subject id of each row, in the order of the values.
    reason : `str`, optional
        The reason every row carries.

    Returns
    -------
    rows : `list` [`types.SimpleNamespace`]
        One row per value.
    """
    return [
        SimpleNamespace(
            value=value,
            status=status,
            reason=reason,
            action_taken=None,
            subject=SimpleNamespace(
                observing_night_id=nights[index] if index < len(nights) else None,
                subject_id=subject_ids[index] if index < len(subject_ids) else f"row-{index}",
            ),
        )
        for index, value in enumerate(values)
    ]


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


# -- The earlier-nights stack (decision D11) ----------------------------------

TWO_NIGHTS = ("2026-10-01", "2026-10-02")
BOTH_FRAMES = ("Both/light_0.fit", "Both/light_1.fit")


def two_night_rows(target: str = "Both", earlier: int = 8) -> list[SimpleNamespace]:
    """Build library weight rows for a stack of two nights.

    Parameters
    ----------
    target : `str`, optional
        The target whose frames the rows name.
    earlier : `int`, optional
        How many frames the first night holds. The second night holds one.

    Returns
    -------
    rows : `list` [`types.SimpleNamespace`]
        A weight row for each frame: ``light_0`` to the one before ``earlier``
        on the first night, then one on the second.
    """
    return rows_for(
        *([1.0] * (earlier + 1)),
        nights=(TWO_NIGHTS[0],) * earlier + (TWO_NIGHTS[1],),
        subject_ids=tuple(f"{target}/light_{index}.fit" for index in range(earlier + 1)),
    )


def give_many_frames(fake: FakeAstrometrics, *target_ids: str, count: int = 9) -> None:
    """Give the named fake targets ``count`` ASI frames, ``light_0`` upward.

    Parameters
    ----------
    fake : `FakeAstrometrics`
        The installed fake. Its target list is replaced by copies, so the
        module's shared targets stay as they were.
    *target_ids : `str`
        The targets to give frames.
    count : `int`, optional
        How many frames each gets.
    """
    copies = [
        SimpleNamespace(
            id=target.id,
            frames=[
                SimpleNamespace(camera=ASI, path=f"{target.id}/light_{index}.fit", filter="L")
                for index in range(count)
            ]
            if target.id in target_ids
            else target.frames,
        )
        for target in fake.targets.list()
    ]
    fake.targets.list = lambda: copies


def run_stack_only(script: Any, tmp_path: Path, *extra: str) -> list[dict]:
    """Run a stack-only pass over the ASI camera and read its progress file.

    Parameters
    ----------
    script : `module`
        The imported script module.
    tmp_path : `pathlib.Path`
        Holds the progress file.
    *extra : `str`
        More command-line options.

    Returns
    -------
    records : `list` [`dict`]
        The progress records, one per target.
    """
    progress_path = tmp_path / "progress.jsonl"
    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--force-restack", "--camera", ASI,
        "--progress-file", str(progress_path), *extra,
    ])  # fmt: skip
    return read_lines(progress_path)


def test_a_stack_of_two_nights_also_gets_its_earlier_nights_stacked(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check only the target with a two-night stack makes the subset."""
    give_many_frames(fake, "Both", "AsiOnly")
    fake.quality_rows = {
        f"Both|{STACKING_FRAME_WEIGHT}": two_night_rows(),
        f"AsiOnly|{STACKING_FRAME_WEIGHT}": rows_for(
            1.0, 1.0, nights=("2026-10-01", "2026-10-01"), subject_ids=("AsiOnly/light_0.fit", "x")
        ),
    }

    records = run_stack_only(script, tmp_path)

    (call,) = fake.earlier_nights_calls
    assert call["target"] == "Both"
    # Only the first night's frames are picked; the last night's is left out.
    assert [frame.path for frame in call["frames"]] == [f"Both/light_{index}.fit" for index in range(8)]
    assert call["label"] == EARLIER_NIGHTS_VARIANT
    assert call["record_forecast_rows"] is True
    assert [(record["target"], record["status"], record["error"]) for record in records] == [
        ("Both", "success", ""),
        ("AsiOnly", "success", ""),
    ]


def test_a_one_night_stack_makes_no_earlier_nights_stack(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check one night, or no stored weights, is not given a subset."""
    fake.quality_rows = {
        f"Both|{STACKING_FRAME_WEIGHT}": rows_for(
            1.0, 1.0, nights=("2026-10-01", "2026-10-01"), subject_ids=BOTH_FRAMES
        )
    }
    run_stack_only(script, tmp_path)

    assert fake.earlier_nights_calls == []


def test_a_failed_earlier_nights_stack_is_noted_and_the_library_stack_still_counts(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check a failure is noted, the status stays success, the run goes on."""
    give_many_frames(fake, "Both", "AsiOnly")
    fake.quality_rows = {
        f"Both|{STACKING_FRAME_WEIGHT}": two_night_rows("Both"),
        f"AsiOnly|{STACKING_FRAME_WEIGHT}": two_night_rows("AsiOnly"),
    }
    fake.earlier_nights_failures = {"Both"}

    records = run_stack_only(script, tmp_path)

    assert [(record["target"], record["status"]) for record in records] == [
        ("Both", "success"),
        ("AsiOnly", "success"),
    ]
    assert "the earlier-nights stack failed" in records[0]["error"]
    assert "the earlier nights of Both broke" in records[0]["error"]
    assert records[1]["error"] == ""
    assert [call["target"] for call in fake.earlier_nights_calls] == ["Both", "AsiOnly"]


def test_no_earlier_nights_skips_the_subset_stack(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --no-earlier-nights leaves the subset out."""
    give_many_frames(fake, "Both")
    fake.quality_rows = {f"Both|{STACKING_FRAME_WEIGHT}": two_night_rows()}

    records = run_stack_only(script, tmp_path, "--no-earlier-nights")

    assert fake.earlier_nights_calls == []
    assert [record["status"] for record in records] == ["success", "success"]


def test_a_stack_that_failed_makes_no_earlier_nights_stack(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check a failed library stack never reaches the subset step."""
    give_many_frames(fake, "Both")
    fake.stack_failures = {"Both"}
    fake.quality_rows = {f"Both|{STACKING_FRAME_WEIGHT}": two_night_rows()}

    run_stack_only(script, tmp_path)

    assert fake.earlier_nights_calls == []


def test_the_review_list_says_whether_the_earlier_nights_rows_are_there(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check the column is yes, no or n/a by the library stack's nights."""
    import csv

    give_many_frames(fake, "Both", "AsiOnly")

    fake.quality_rows = {
        f"Both|{STACKING_FRAME_WEIGHT}": two_night_rows("Both"),
        f"Both|{STACKING_FRAME_WEIGHT}|{EARLIER_NIGHTS_VARIANT}": rows_for(1.0),
        f"Both|{STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT}|{EARLIER_NIGHTS_VARIANT}": rows_for(
            2.0e-3
        ),
        f"AsiOnly|{STACKING_FRAME_WEIGHT}": two_night_rows("AsiOnly"),
        f"AsiOnly|{STACKING_FRAME_WEIGHT}|{EARLIER_NIGHTS_VARIANT}": rows_for(1.0),
    }
    review_path = tmp_path / "review.csv"

    run_stack_only(script, tmp_path, "--review-list", str(review_path))

    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    # Both has the subset's weights and noise; AsiOnly lacks the noise.
    assert rows["Both"]["earlier_nights_present"] == "yes"
    assert rows["AsiOnly"]["earlier_nights_present"] == "no"


def test_a_first_stack_of_fewer_than_eight_frames_is_not_made_and_the_review_says_so(
    script: Any, fake: FakeAstrometrics, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check 7 earlier frames skip the stack, 8 make it, and say so."""
    import csv

    give_many_frames(fake, "Both", "AsiOnly")
    fake.quality_rows = {
        f"Both|{STACKING_FRAME_WEIGHT}": two_night_rows("Both", earlier=7),
        f"AsiOnly|{STACKING_FRAME_WEIGHT}": two_night_rows("AsiOnly", earlier=8),
    }
    review_path = tmp_path / "review.csv"

    records = run_stack_only(script, tmp_path, "--review-list", str(review_path))

    assert [call["target"] for call in fake.earlier_nights_calls] == ["AsiOnly"]
    assert [record["error"] for record in records] == ["", ""]
    assert "[Both] earlier-nights stack skipped (7 frames, fewer than 8)" in capsys.readouterr().out
    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    assert rows["Both"]["earlier_nights_present"] == "skipped (7 frames, fewer than 8)"
    assert rows["AsiOnly"]["earlier_nights_present"] == "no"


def test_the_review_list_marks_a_one_night_or_refused_target_not_applicable(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check one night, no weights and a refusal all give n/a."""
    import csv

    fake.quality_rows = {f"Both|{STACKING_FRAME_WEIGHT}": rows_for(1.0, 1.0, nights=("2026-10-01",) * 2)}
    review_path = tmp_path / "review.csv"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--progress-file", str(tmp_path / "progress.jsonl"),
        "--review-list", str(review_path),
    ])  # fmt: skip

    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    assert {name: row["earlier_nights_present"] for name, row in rows.items()} == {
        "AsiOnly": "n/a",
        "Both": "n/a",
        "NikonOnly": "n/a",
    }


# -- The odd–even difference step (decision of 2026-10-26) ----------------


def noise_rows(target: str, ratio: float | None) -> dict[str, list[SimpleNamespace]]:
    """Build the measured and predicted noise rows of a library stack.

    Parameters
    ----------
    target : `str`
        The target.
    ratio : `float` or `None`
        Measured over predicted noise; `None` leaves the measured row out.

    Returns
    -------
    rows : `dict` [`str`, `list`]
        Fake quality rows, keyed as `query_quality` reads them.
    """
    rows = {f"{target}|{STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT}": rows_for(1.0e-3)}
    if ratio is not None:
        rows[f"{target}|{STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT}"] = rows_for(ratio * 1.0e-3)
    return rows


@pytest.mark.parametrize("ratio", [0.9, 1.4, 2.95, None])
def test_the_odd_even_difference_runs_whatever_the_ratio_or_without_a_row(
    script: Any, fake: FakeAstrometrics, tmp_path: Path, ratio: float | None
) -> None:
    """Check every stack gets the measurement (D19); no ratio gates it."""
    fake.quality_rows = noise_rows("Both", ratio)

    records = run_stack_only(script, tmp_path)

    assert fake.odd_even_difference_calls == ["Both", "AsiOnly"]
    assert [record["status"] for record in records] == ["success", "success"]


def test_the_odd_even_difference_line_prints_the_stored_ratio_first(
    script: Any, fake: FakeAstrometrics, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Check the stored ratio is read before the two noise rows."""
    fake.quality_rows = {
        **noise_rows("Both", 1.4),
        f"Both|{STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO}": rows_for(1.5),
        **noise_rows("AsiOnly", 2.95),
    }

    run_stack_only(script, tmp_path)

    output = capsys.readouterr().out
    assert "[Both] odd–even difference run (ratio 1.50)" in output
    assert "[AsiOnly] odd–even difference run (ratio 2.95)" in output


def test_a_failed_odd_even_difference_is_noted_and_the_run_goes_on(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check the failure is in the error text, the status stays success."""
    fake.quality_rows = {**noise_rows("Both", 1.4), **noise_rows("AsiOnly", 1.4)}
    fake.odd_even_difference_failures = {"Both"}

    records = run_stack_only(script, tmp_path)

    assert [record["status"] for record in records] == ["success", "success"]
    assert "the odd–even difference measurement failed" in records[0]["error"]
    assert "the odd–even difference of Both broke" in records[0]["error"]
    assert fake.odd_even_difference_calls == ["Both", "AsiOnly"]


def test_no_odd_even_difference_skips_the_measurement(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --no-odd-even-difference leaves the measurement out."""
    fake.quality_rows = noise_rows("Both", 1.4)

    records = run_stack_only(script, tmp_path, "--no-odd-even-difference")

    assert fake.odd_even_difference_calls == []
    assert records[0]["error"] == ""


def test_a_stack_that_failed_makes_no_odd_even_difference(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check a failed stack never reaches the measurement."""
    fake.quality_rows = noise_rows("Both", 1.4)
    fake.stack_failures = {"Both"}

    run_stack_only(script, tmp_path)

    assert fake.odd_even_difference_calls == ["AsiOnly"]


def test_the_review_list_says_what_happened_to_the_odd_even_difference(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check the column says run, or not run for too few frames."""
    import csv

    few = "a half would have 5 frames, fewer than the 8 the odd–even difference noise needs"
    fake.quality_rows = {
        f"Both|{STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT}": rows_for(1.0e-3),
        f"AsiOnly|{STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT}": rows_for(
            None, status="not_measured", reason=few
        ),
    }
    review_path = tmp_path / "review.csv"

    run_stack_only(script, tmp_path, "--review-list", str(review_path))

    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    assert rows["Both"]["odd_even_difference"] == "run"
    assert rows["AsiOnly"]["odd_even_difference"] == "not run (fewer than 8 frames per half)"


def test_the_review_list_gives_the_reason_of_a_failed_odd_even_difference(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check failed carries the stored reason, or says no row was stored."""
    import csv

    fake.quality_rows = {
        f"Both|{STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT}": rows_for(
            None, status="not_measured", reason="the stacker failed on the odd half"
        ),
    }
    review_path = tmp_path / "review.csv"

    script.run_full_processing([
        "--skip-reindex", "--stack-only", "--progress-file", str(tmp_path / "progress.jsonl"),
        "--review-list", str(review_path),
    ])  # fmt: skip

    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    assert {name: row["odd_even_difference"] for name, row in rows.items()} == {
        "AsiOnly": "failed: no result row was stored",
        "Both": "failed: the stacker failed on the odd half",
        "NikonOnly": "n/a",
    }


def test_the_review_list_says_when_the_odd_even_difference_was_switched_off(
    script: Any, fake: FakeAstrometrics, tmp_path: Path
) -> None:
    """Check --no-odd-even-difference shows in the column, not as a failure."""
    import csv

    review_path = tmp_path / "review.csv"

    run_stack_only(script, tmp_path, "--no-odd-even-difference", "--review-list", str(review_path))

    with open(review_path, newline="") as handle:
        rows = {row["target"]: row for row in csv.DictReader(handle)}
    assert rows["Both"]["odd_even_difference"] == "skipped (--no-odd-even-difference)"
