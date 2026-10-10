"""Tests for the script that moves set-aside frames back into their targets.

The work itself is `ProcessingPipelines.restore_excluded_frames`, tested in
`api/test/test_excluded_frames_api.py`. These tests check that the script
passes its options through and saves only when it moved something.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from astrometricslib.models.excluded_frames import RestoreReport, SetAsideFrame
from astrometricslib.scripts import restore_excluded_frames

FRAME = SetAsideFrame(
    file="frame_001.fits",
    original_path="/lights/M 1/frame_001.fits",
    kind="clouded",
    reason="203 stars against a typical 3348",
    star_count=203,
    typical_star_count=3348.0,
    roundness=0.88,
    sky_median_adu=5000.0,
)


def run_script(arguments: list[str], applied_count: int) -> MagicMock:
    """Run the script against a stand-in library holding one target.

    Parameters
    ----------
    arguments : `list` [`str`]
        Command-line arguments.
    applied_count : `int`
        How many frames the stand-in says it moved back.

    Returns
    -------
    astrometrics : `MagicMock`
        The stand-in library, to inspect what the script called.
    """
    astrometrics = MagicMock()
    target = SimpleNamespace(id="M 1")
    astrometrics.targets.list.return_value = [target]
    astrometrics.targets.get.return_value = target
    astrometrics.processing.restore_excluded_frames.side_effect = lambda target, apply: RestoreReport(
        target_id=target.id,
        applied=apply,
        frames=[FRAME],
        restored_count=applied_count if apply else 0,
    )
    with patch.object(restore_excluded_frames, "Astrometrics", return_value=astrometrics):
        restore_excluded_frames.main(arguments)
    return astrometrics


def test_without_apply_the_script_only_lists_and_never_saves() -> None:
    """The default run is a dry run."""
    astrometrics = run_script([], applied_count=1)

    call = astrometrics.processing.restore_excluded_frames.call_args
    assert call.kwargs["apply"] is False
    astrometrics.targets.save.assert_not_called()


def test_with_apply_the_script_restores_and_saves() -> None:
    """``--apply`` restores each target and saves the catalog once."""
    astrometrics = run_script(["--apply"], applied_count=1)

    call = astrometrics.processing.restore_excluded_frames.call_args
    assert call.kwargs["apply"] is True
    astrometrics.targets.save.assert_called_once()


def test_with_apply_and_nothing_restored_the_script_does_not_save() -> None:
    """A target with nothing to restore leaves the catalog alone."""
    astrometrics = run_script(["--apply"], applied_count=0)

    astrometrics.targets.save.assert_not_called()
