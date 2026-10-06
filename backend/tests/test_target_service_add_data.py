"""Unit tests for TargetService.add_target_data input checks.

A bad image file raises InvalidArgumentError before any target is
created, instead of returning an error dictionary.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib import InvalidArgumentError
from backend.services.data.target_service import TargetService


def test_add_target_data_rejects_a_non_path() -> None:
    """An image file that is not a path raises and creates no target."""
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()

    with pytest.raises(InvalidArgumentError, match="file path"):
        service.add_target_data("M31", image_file=42)

    service.astrometrics.targets.create.assert_not_called()
