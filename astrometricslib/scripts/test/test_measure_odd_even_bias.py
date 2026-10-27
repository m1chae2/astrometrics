"""Tests for the odd–even bias script (D16): output and JSON file."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.pipelines.stacking.odd_even_bias import BiasMeasurement, BiasRepeat
from astrometricslib.scripts import measure_odd_even_bias as script


@pytest.fixture
def fake_library(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the library and the measurement with stand-ins.

    Returns
    -------
    calls : `list` [`dict`]
        The keyword arguments of each measurement call.
    """
    calls: list[dict[str, Any]] = []
    target = SimpleNamespace(id="M 99")
    astrometrics = SimpleNamespace(
        targets=SimpleNamespace(get=lambda name: target),
        config=object(),
        processing=SimpleNamespace(acquire_stacking_slot=lambda: None),
    )

    def fake_measure(target: Any, library_stack_path: str, **keywords: Any) -> BiasMeasurement:
        """Record the call and return a fixed result.

        Parameters
        ----------
        target : `Any`
            The target.
        library_stack_path : `str`
            The library stack.
        **keywords
            The other arguments.

        Returns
        -------
        measurement : `BiasMeasurement`
            A result with one repeat.
        """
        calls.append({"path": library_stack_path, **keywords})
        repeat = BiasRepeat(0.98e-3, 1e-3, 0.98, 0.02, 2e-3, 1.0, 20.0, 100)
        return BiasMeasurement("M 99", "Cam", 0.98, 0.02, [repeat], 24, 12, 13, "inverse_variance")

    monkeypatch.setattr(script, "Astrometrics", lambda: astrometrics)
    monkeypatch.setattr(script, "stack_path_of", lambda target, kind: "/stacks/M99.fits")
    monkeypatch.setattr(script, "measure_odd_even_bias", fake_measure)
    return calls


def test_the_script_prints_the_ratio_and_writes_the_json_file(
    fake_library: list[dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The result is printed in words and written where the user said."""
    json_path = tmp_path / "out" / "bias.json"

    status = script.main([
        "M 99",
        "--work-folder",
        str(tmp_path / "work"),
        "--repeats",
        "3",
        "--json",
        str(json_path),
    ])

    assert status == 0
    (call,) = fake_library
    assert call["path"] == "/stacks/M99.fits" and call["repeats"] == 3
    assert call["work_folder"] == str(tmp_path / "work")
    output = capsys.readouterr().out
    assert "difference noise over true noise: 0.9800 +/- 0.0200" in output
    assert "below 1: the difference reads low" in output
    data = json.loads(json_path.read_text())
    assert data["ratio"] == pytest.approx(0.98) and data["camera"] == "Cam" and len(data["repeats"]) == 1


def test_without_a_json_path_nothing_is_written(
    fake_library: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file is written unless asked; the work folder defaults to temp."""
    monkeypatch.chdir(tmp_path)
    scratch = tmp_path.parent / (tmp_path.name + "_scratch")
    monkeypatch.setattr(script.tempfile, "mkdtemp", lambda prefix: str(scratch / (prefix + "x")))

    script.main(["M 99"])

    assert list(tmp_path.iterdir()) == []
    assert "odd_even_bias_" in fake_library[0]["work_folder"]


def test_fewer_than_one_repeat_is_refused(fake_library: list[dict[str, Any]]) -> None:
    """A repeat count below 1 returns a usage error and measures nothing."""
    assert script.main(["M 99", "--repeats", "0"]) == 2
    assert fake_library == []
