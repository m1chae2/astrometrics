"""Tests for how the batch script picks its camera passes.

The script used to have two cameras written into it. It now takes them from
the config's setups. These tests use stand-in objects, so they never touch the
real library, the real config, or Siril.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.utilities.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig

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
    return SimpleNamespace(id=target_id, frames=[SimpleNamespace(camera=camera) for camera in cameras])


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
        self.config = SimpleNamespace(
            get_observatory_setups=lambda: setups,
            get_primary_camera_name=lambda: ASI,
            get_primary_focal_length_mm=lambda: None,
        )
        self.calls: list[dict[str, Any]] = []

    def process_all_targets(self, **keywords: Any) -> SimpleNamespace:
        """Record one pass and pretend it succeeded.

        Returns
        -------
        summary : `types.SimpleNamespace`
            An empty pass summary.
        """
        self.calls.append(keywords)
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
    """Check that each pass gets the full camera name and only its targets."""
    fake = FakeAstrometrics(TARGETS, SETUPS)
    monkeypatch.setattr(script, "Astrometrics", lambda: fake)

    script.run_full_processing(["--skip-reindex"])

    assert [(call["camera_name"], call["target_ids"]) for call in fake.calls] == [
        (ASI, ["Both", "AsiOnly"]),
        (NIKON, ["NikonOnly"]),
    ]


def test_a_config_without_setups_stops_with_a_clear_message(
    script: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check that an older config is told what to add, and nothing is run."""
    fake = FakeAstrometrics(TARGETS, ObservatorySetups())
    monkeypatch.setattr(script, "Astrometrics", lambda: fake)

    script.run_full_processing(["--skip-reindex"])

    assert "[Observatory.Setups]" in capsys.readouterr().out
    assert fake.calls == []
