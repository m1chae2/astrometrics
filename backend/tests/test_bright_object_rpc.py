"""Purpose: Test the two RPC methods that choose the masked stretch's object.

Description: ``processing:list_bright_objects`` and
``processing:set_bright_object`` are served by `StackReviewService`, which
adapts the app's parameters to the library: the list is
``processing.enhancement.get_bright_objects`` (``refresh`` becomes ``force``)
and the choice is saved with ``targets.update(target,
preview_bright_object=...)``. The tests check the routing, that the replies are
plain JSON, that a name in any spelling picks the listed object, and that a
bad choice is refused, saves nothing and becomes an ``invalid_argument``
error reply.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import PreviewBrightObject, Target
from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.models.processing_results import BrightObjectChoice, BrightObjectChoices
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers import rpc_router
from backend.routers.rpc_router import rpc_registry
from backend.services.processing.stack_review_service import StackReviewService
from backend.services.rpc_protocol import RPCRequest, serialize_rpc_result


def listing_with_nebula() -> BrightObjectChoices:
    """Build a stack's list holding one nebula.

    Returns
    -------
    listing : `BrightObjectChoices`
        The list, as the library gives it.
    """
    nebula = BrightObjectChoice(
        simbad_id="NGC 7635",
        object_type="HII",
        category="nebula",
        ra_deg=350.2,
        dec_deg=61.2,
        major_axis_arcmin=15.0,
    )
    return BrightObjectChoices(target_id="M 52", objects=[nebula])


def make_service() -> tuple[StackReviewService, SimpleNamespace]:
    """Build the service over a stand-in library that holds the target M 52.

    Returns
    -------
    service, library : `tuple`
        The service and the stand-in library (``targets.update`` and
        ``processing.enhancement.get_bright_objects`` are mocks).
    """
    target = Target(id="M 52")
    library = SimpleNamespace(
        targets=SimpleNamespace(
            get=lambda target_id: target if target_id == "M 52" else None, update=MagicMock()
        ),
        processing=SimpleNamespace(
            enhancement=SimpleNamespace(get_bright_objects=MagicMock(return_value=listing_with_nebula()))
        ),
    )
    return StackReviewService(library, wait_seconds=0.1), library


def test_the_methods_are_declared_and_routed_to_the_stack_review_service() -> None:
    """Both methods are public and served by the stack review service."""
    assert "processing:list_bright_objects" in RPC_METHODS
    assert "processing:set_bright_object" in RPC_METHODS
    assert rpc_registry._handlers["processing:list_bright_objects"] == (
        "stack_review_service",
        "list_bright_objects",
    )
    assert rpc_registry._handlers["processing:set_bright_object"] == (
        "stack_review_service",
        "set_bright_object",
    )


def test_the_list_asks_the_enhancement_pipeline_and_comes_back_as_plain_json() -> None:
    """``refresh`` becomes the library's ``force``; the reply is plain data."""
    service, library = make_service()

    plain = serialize_rpc_result(service.list_bright_objects("M 52", refresh=True))

    (target,), keywords = library.processing.enhancement.get_bright_objects.call_args
    assert target.id == "M 52"
    assert keywords == {"force": True}
    assert plain["objects"][0]["simbad_id"] == "NGC 7635"
    assert plain["message"] is None


def test_an_object_choice_is_saved_on_the_target_and_comes_back() -> None:
    """The object's name, position and size are saved by ``targets.update``."""
    service, library = make_service()

    saved = service.set_bright_object(
        "M 52", "object", simbad_id="NGC  7635", ra_deg=350.2, dec_deg=61.2, major_axis_arcmin=9.0
    )

    assert saved == PreviewBrightObject(
        mode="object", simbad_id="NGC  7635", ra_deg=350.2, dec_deg=61.2, major_axis_arcmin=9.0
    )
    (target,), keywords = library.targets.update.call_args
    assert target.id == "M 52"
    assert keywords == {"preview_bright_object": saved}
    assert serialize_rpc_result(saved)["simbadId"] == "NGC  7635"


def test_off_and_automatic_save_no_object() -> None:
    """Off and Automatic save the mode alone."""
    service, library = make_service()
    assert service.set_bright_object("M 52", "off") == PreviewBrightObject(mode="off")
    assert service.set_bright_object("M 52", "auto") == PreviewBrightObject(mode="auto")
    assert library.targets.update.call_count == 2


@pytest.mark.parametrize("name", ["ngc7635", "NGC  7635", "NAME NGC_7635"])
def test_an_object_name_in_any_spelling_saves_the_listed_object(name: str) -> None:
    """A name is found in the stack's list; its position and size are saved."""
    service, _ = make_service()

    saved = service.set_bright_object("M 52", "object", name=name)

    assert (saved.simbad_id, saved.ra_deg, saved.dec_deg, saved.major_axis_arcmin) == (
        "NGC 7635",
        350.2,
        61.2,
        15.0,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"mode": "sometimes"},
        {"mode": "object"},
        {"mode": "object", "simbad_id": "X", "ra_deg": 400.0, "dec_deg": 0.0},
        {"mode": "object", "simbad_id": "X", "ra_deg": 10.0, "dec_deg": 0.0, "major_axis_arcmin": -1.0},
        {"mode": "object", "name": "M 31"},
    ],
)
def test_a_bad_choice_is_refused_and_nothing_is_saved(arguments: dict) -> None:
    """A bad mode, a missing position, a bad size or a stray name raises."""
    service, library = make_service()

    with pytest.raises(InvalidArgumentError):
        service.set_bright_object("M 52", **arguments)
    library.targets.update.assert_not_called()


@pytest.mark.anyio
async def test_a_bad_choice_is_an_invalid_argument_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """``InvalidArgumentError`` reaches the caller with its code."""
    service, _ = make_service()
    monkeypatch.setattr(container, "stack_review_service", service)
    response = await rpc_router.handle_rpc(
        RPCRequest(method="processing:set_bright_object", id=1, params={"target": "M 52", "mode": "object"})
    )
    assert json.loads(bytes(response.body))["error"]["data"]["code"] == "invalid_argument"
