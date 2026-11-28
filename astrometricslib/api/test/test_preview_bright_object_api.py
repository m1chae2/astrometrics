"""Tests for `processing.enhancement.get_bright_objects`, the stretch's list.

``get_bright_objects`` lists what SIMBAD places inside a stack, with the
choice saved on the target (``current``). The choice itself is a field of the
target, set with ``targets.update``; the backend's
``processing:set_bright_object`` adapter and its tests check the choice. A
stand-in SIMBAD driver and a stand-in target catalog keep the tests offline.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import PreviewBrightObject
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.processing_results import BrightObjectChoices
from astrometricslib.models.target import Target
from astrometricslib.pipelines.stacking.post_processing import bright_object_choices


def make_pipelines(simbad: MagicMock | None = None) -> ProcessingPipelines:
    """Build pipelines with a stand-in target catalog.

    Parameters
    ----------
    simbad : `unittest.mock.MagicMock`, optional
        The SIMBAD driver to use.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The pipelines.
    """
    return ProcessingPipelines(
        AppConfiguration(), MagicMock(), targets=MagicMock(), drivers=Drivers(simbad=simbad)
    )


def target_with_plain_stack(tmp_path: Path) -> Target:
    """Make a target whose stack has no plate solution.

    Returns
    -------
    target : `Target`
        The target M 52 with a 20 by 20 pixel stack.
    """
    stack = tmp_path / "stack.fits"
    fits.PrimaryHDU(np.zeros((20, 20), dtype=np.float32)).writeto(stack)
    target = Target(id="M 52")
    target.stacking.stacked_image = str(stack)
    return target


def test_a_target_with_no_wcs_in_its_stack_is_told_to_run_astrometry(tmp_path: Path) -> None:
    """The list call gives the message and does not ask SIMBAD."""
    simbad = MagicMock()

    result = make_pipelines(simbad).enhancement.get_bright_objects(target_with_plain_stack(tmp_path))

    assert result.objects == []
    assert result.message == "Run Astrometry first to list objects in the frame"
    simbad.query_tap.assert_not_called()


def test_the_list_carries_the_saved_choice(tmp_path: Path) -> None:
    """The list call says which choice the target has saved."""
    target = target_with_plain_stack(tmp_path)
    target.preview_bright_object = PreviewBrightObject(mode="off")

    result = make_pipelines(MagicMock()).enhancement.get_bright_objects(target)

    assert result.current is not None
    assert result.current.mode == "off"


@pytest.mark.parametrize(("force", "use_cache"), [(False, True), (True, False)])
def test_force_asks_simbad_again(monkeypatch: pytest.MonkeyPatch, force: bool, use_cache: bool) -> None:
    """``force`` reads past the stored SIMBAD answer."""
    asked = {}

    def list_choices(
        target_id: str, stacked_path: str, simbad: object, *, use_cache: bool
    ) -> BrightObjectChoices:
        """Note whether the stored answer may be used.

        Returns
        -------
        choices : `BrightObjectChoices`
            An empty list.
        """
        asked["use_cache"] = use_cache
        return BrightObjectChoices(target_id=target_id)

    monkeypatch.setattr(bright_object_choices, "list_bright_object_choices", list_choices)
    make_pipelines().enhancement.get_bright_objects(Target(id="M 52"), force=force)
    assert asked == {"use_cache": use_cache}
