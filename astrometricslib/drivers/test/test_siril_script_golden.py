"""Purpose: Pin the Siril scripts and run records `process_target` produces.

Description: Runs `ImageProcessing.process_target` with Siril stubbed out
for a set of representative inputs and compares what it hands to Siril (the
command lists, in order), the arguments of the output-reading call and the
run diagnostics with a stored copy. The stored copy was made from the code
before the Siril driver was split into a package, so this test shows that the
split changed none of the text Siril receives and none of the values the
stack quality rows are built from.

To remake the stored copy after an intended change, run this file with the
environment variable ``UPDATE_SIRIL_GOLDEN=1``.
"""

import io
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.interfaces.stacking_driver import PipelineSteps
from astrometricslib.drivers.siril_interface import imaging_stacker, spectral_stacker

GOLDEN_PATH = Path(__file__).parent / "golden" / "siril_scripts.json"


class _FlatAssessment:
    """A stand-in flat assessment that asks for a smoothed master flat."""

    smoothing_sigma_pixels = 2.5

    def as_diagnostics(self) -> dict[str, Any]:
        """Give the diagnostics the real assessment would record.

        Returns
        -------
        diagnostics : `dict`
            A fixed record.
        """
        return {"needs_smoothing": True, "smoothing_sigma_pixels": 2.5}


class _NullContext:
    """A context manager that does nothing, standing in for the Siril lock."""

    def __enter__(self) -> _NullContext:
        """Enter the no-op context.

        Returns
        -------
        context : `_NullContext`
            The context itself.
        """
        return self

    def __exit__(self, *exception_details: object) -> bool:
        """Leave the no-op context without hiding errors.

        Returns
        -------
        suppressed : `bool`
            Always `False`.
        """
        return False


def _align_one_frame(paths: list[str], directory: str) -> tuple[list[str], dict[str, int]]:
    """Stand in for the pipeline's alignment step: one frame aligns.

    Returns
    -------
    aligned_paths : `list` [`str`]
        One made-up aligned frame.
    counts : `dict` [`str`, `int`]
        One frame registered, none failed.
    """
    return ["b.fits"], {"registered": 1, "failed": 0}


# Each case: counts of staged files (biases, darks, flats, lights), sensor
# kind, configuration and request options.
CASES: dict[str, dict[str, Any]] = {
    "imaging_full_calibration_adaptive": {
        "counts": (5, 5, 5, 20),
        "cfa": False,
        "options": {"filter_wfwhm": "90%", "filter_round": "80%", "stack_weight": "wfwhm"},
        "config": {"mode": "adaptive", "rejmap": True},
        "flat_assessment": True,
    },
    "imaging_color_sensor_reference_frame": {
        "counts": (5, 5, 5, 20),
        "cfa": True,
        "options": {"reference_frame_path": "/frames/light_0007.fits", "stack_weight": "noise"},
        "config": {"mode": "fixed", "rejmap": False},
    },
    "imaging_cached_masters_override_sigma": {
        "counts": (4, 4, 4, 12),
        "cfa": False,
        "options": {"rejection_sigma": (2.5, 3.5), "filter_wfwhm": "100%"},
        "config": {"mode": "adaptive", "rejmap": False},
        "restored": {"bias", "dark", "flat"},
    },
    "imaging_no_calibration": {
        "counts": (0, 0, 0, 10),
        "cfa": False,
        "options": {},
        "config": {"mode": "adaptive", "rejmap": True},
    },
    "imaging_bias_only_with_few_frames": {
        "counts": (1, 0, 2, 4),
        "cfa": False,
        "options": {"filter_wfwhm": "50%"},
        "config": {"mode": "adaptive", "rejmap": False},
    },
    "single_light": {
        "counts": (3, 3, 3, 1),
        "cfa": True,
        "options": {},
        "config": {"mode": "fixed", "rejmap": False},
    },
    "spectral_standard": {
        "counts": (5, 5, 5, 20),
        "cfa": False,
        "options": {"is_spectral": True, "filter_wfwhm": "90%", "stack_weight": "wfwhm"},
        "config": {"mode": "adaptive", "rejmap": True},
    },
    "spectral_alternate_detection": {
        "counts": (0, 3, 0, 8),
        "cfa": False,
        "options": {"is_spectral": True, "spectral_star_detection": "relaxed"},
        "config": {"mode": "fixed", "rejmap": False},
    },
    "spectral_phase_correlation": {
        "counts": (2, 2, 2, 20),
        "cfa": False,
        "options": {
            "is_spectral": True,
            "spectral_star_detection": "phase_correlation",
            "stack_weight": "wfwhm",
        },
        "config": {"mode": "fixed", "rejmap": True},
        "phase_correlation": True,
    },
}


def _json_safe(value: Any) -> Any:
    """Turn a recorded value into plain JSON data.

    Returns
    -------
    plain : `Any`
        The value with sets as sorted lists and other objects as text.
    """

    def convert(item: Any) -> Any:
        """Convert one value that JSON cannot hold.

        Returns
        -------
        converted : `Any`
            A sorted list for a set, otherwise text.
        """
        return sorted(item) if isinstance(item, set) else str(item)

    return json.loads(json.dumps(value, default=convert))


def _name_patches(uses_color_filter_array: bool, phase_correlation: bool) -> list[tuple[Any, str, Any]]:
    """List the module-level names to replace where the driver uses them.

    Returns
    -------
    patches : `list` [`tuple`]
        ``(module, attribute, replacement)`` entries.
    """
    patches: list[tuple[Any, str, Any]] = [
        (imaging_stacker, "_frames_use_color_filter_array", lambda path: uses_color_filter_array),
        (imaging_stacker, "siril_process_lock", lambda **kwargs: _NullContext()),
    ]
    if phase_correlation:
        patches.append((spectral_stacker, "find_calibrated_frame_paths", lambda directory, seq: ["a.fits"]))
    return patches


def _call_process_target(driver: Any, **request_fields: Any) -> str | None:
    """Call `process_target` with the given request fields.

    Returns
    -------
    result : `str` or `None`
        What `process_target` returned.
    """
    return driver.process_target(**request_fields)


def _run_case(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run `process_target` for one case with Siril stubbed out.

    Returns
    -------
    record : `dict`
        The scripts sent, reader calls, result and diagnostics.
    """
    case = CASES[name]
    biases, darks, flats, lights = case["counts"]
    sent_scripts: list[list[str]] = []
    read_calls: list[dict[str, Any]] = []

    def fake_send_commands(self: Any, command_pipe: Any, commands: Any, **kwargs: Any) -> None:
        """Record the commands instead of sending them."""
        sent_scripts.append(list(commands))

    def fake_read_output(self: Any, output_pipe: Any, target_folder: Any, *args: Any, **kwargs: Any) -> None:
        """Record the reader's arguments instead of reading Siril's output."""
        read_calls.append({
            "positional": [str(argument) for argument in args],
            "keywords": {
                key: str(value)
                for key, value in sorted(kwargs.items())
                if key not in {"job_logger", "status_queue", "process"}
            },
        })

    def fake_build_directories(
        self: Any, id: str, image_files: Any, camera_filter: Any = None, job_logger: Any = None
    ) -> str:
        """Make an empty work folder with the wanted file counts.

        Returns
        -------
        target_folder : `str`
            The work folder.
        """
        target_folder = tmp_path / "work" / id
        for subdirectory in ("biases", "darks", "flats", "lights", "process"):
            (target_folder / subdirectory).mkdir(parents=True, exist_ok=True)
        for subdirectory, count in (
            ("biases", biases),
            ("darks", darks),
            ("flats", flats),
            ("lights", lights),
        ):
            for index in range(count):
                (target_folder / subdirectory / f"{subdirectory}_{index:05d}.fits").touch()
        self.last_run_diagnostics["symlinked_light_paths"] = [
            f"/frames/light_{index:04d}.fits" for index in range(lights)
        ]
        return str(target_folder)

    processing_class = siril_interface.ImageProcessing
    monkeypatch.setattr(processing_class, "build_directories", fake_build_directories)
    monkeypatch.setattr(processing_class, "send_commands", fake_send_commands)
    monkeypatch.setattr(processing_class, "create_named_pipes", lambda self, base: ("cmd", "out"))
    monkeypatch.setattr(
        processing_class, "run_siril_headless", lambda self, *a, **k: MagicMock(stdout=io.BytesIO())
    )
    monkeypatch.setattr(processing_class, "read_output", fake_read_output)
    monkeypatch.setattr(processing_class, "_kill_process_tree", lambda self, *a, **k: None)
    monkeypatch.setattr(processing_class, "cleanup_subprocesses", lambda self: None)
    monkeypatch.setattr(
        processing_class,
        "restore_cached_calibration_masters",
        lambda self, *a, **k: set(case.get("restored", ())),
    )
    monkeypatch.setattr(
        processing_class,
        "assess_staged_flats",
        lambda self, *a, **k: _FlatAssessment() if case.get("flat_assessment") else None,
    )
    for module, attribute, replacement in _name_patches(case["cfa"], case.get("phase_correlation", False)):
        monkeypatch.setattr(module, attribute, replacement)

    mock_config = MagicMock()
    mock_config.get_siril_executable.return_value = "siril"
    mock_config.get_logs_path.return_value = str(tmp_path)
    mock_config.get_frames_path.return_value = str(tmp_path / "frames")
    mock_config.get_stacks_path.return_value = str(tmp_path / "frames")
    mock_config.get_stack_rejection_sigma_mode.return_value = case["config"]["mode"]
    mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
    mock_config.get_stack_rejection_sigma_floor.return_value = 2.0
    mock_config.get_stack_rejection_low_extra_sigma.return_value = 0.5
    mock_config.get_stack_weight.return_value = "wfwhm"
    mock_config.get_stack_generate_rejmap.return_value = case["config"]["rejmap"]
    mock_config.get_auto_open_siril_gui.return_value = False
    mock_config.get_stack_filter_wfwhm_percentile.return_value = "95%"
    mock_config.get_stack_filter_round_percentile.return_value = None
    mock_config.get_minimum_calibration_frames.return_value = 3

    driver = processing_class(mock_config, MagicMock())
    driver.workdir = str(tmp_path / "work")
    result = _call_process_target(
        driver,
        id="GoldenTarget",
        image_files=[{"path": "a.fits", "camera": "Cam", "exposure": "30"}],
        output_file="golden_stack.fits",
        steps=PipelineSteps(
            assess_flats=lambda paths, camera: None,
            align_frames=_align_one_frame if case.get("phase_correlation") else None,
        ),
        **case["options"],
    )
    return _json_safe({
        "result": result,
        "scripts": sent_scripts,
        "read_output_calls": read_calls,
        "diagnostics": {
            key: value
            for key, value in driver.last_run_diagnostics.items()
            if key != "stacking_duration_seconds"
        },
    })


@pytest.mark.parametrize("name", sorted(CASES))
def test_process_target_matches_stored_scripts_and_records(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scripts, reader arguments and diagnostics equal the stored copy."""
    record = _run_case(name, tmp_path, monkeypatch)
    # The work folder is a temporary path; compare with it replaced.
    record = json.loads(json.dumps(record).replace(str(tmp_path), "<TMP>"))
    stored: dict[str, Any] = json.loads(GOLDEN_PATH.read_text()) if GOLDEN_PATH.exists() else {}
    if os.environ.get("UPDATE_SIRIL_GOLDEN") == "1":
        stored[name] = record
        GOLDEN_PATH.parent.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(stored, indent=1) + "\n")
        return
    assert record == stored[name]
    assert list(record["diagnostics"]) == list(stored[name]["diagnostics"]), "diagnostic order changed"
