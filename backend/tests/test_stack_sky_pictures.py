"""Purpose: Unit tests for the Planetarium's list of stack pictures on the sky.

Description: The library places each target's picture
(`Visualization.get_sky_picture`) and keeps the answer until a file changes.
``planetarium:get_stack_pictures`` lists the targets that have a placed
picture, in the camelCase keys the Planetarium reads, with each target's id.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.services.data.stellar_service import StellarService


def test_only_placed_pictures_are_listed_with_their_target() -> None:
    """A target with no placement is left out; the others carry their id."""
    placed = MagicMock()
    placed.model_dump.return_value = {
        "picturePath": "/pictures/M_31.jpg",
        "width": 4,
        "height": 3,
        "gridSize": 2,
        "grid": [],
    }
    astrometrics = MagicMock()
    astrometrics.targets.query.return_value = SimpleNamespace(
        items=[SimpleNamespace(id="M 31"), SimpleNamespace(id="M 1")]
    )
    astrometrics.visualization.get_sky_picture.side_effect = lambda target: (
        placed if target.id == "M 31" else None
    )

    pictures = StellarService(MagicMock(), astrometrics=astrometrics).get_stack_sky_pictures()

    assert pictures == [
        {
            "picturePath": "/pictures/M_31.jpg",
            "width": 4,
            "height": 3,
            "gridSize": 2,
            "grid": [],
            "targetId": "M 31",
        }
    ]
    placed.model_dump.assert_called_with(by_alias=True)
