"""Purpose: Test the two RPC methods that choose the masked stretch's object.

Description: ``processing:list_bright_objects`` and
``processing:set_bright_object`` are served by the library's processing API.
The tests check that they are declared and routed there, that the replies are
plain JSON, that the choice reaches the library with its inputs, and that a
bad choice becomes an ``invalid_argument`` error reply.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.models.processing_results import BrightObjectChoice, BrightObjectChoices
from astrometricslib.models.target import PreviewBrightObject
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers import rpc_router
from backend.routers.rpc_router import rpc_registry
from backend.services.rpc_protocol import RPCRequest, serialize_rpc_result


def test_the_methods_are_declared_and_routed_to_the_processing_api() -> None:
    """Both methods are public and call the processing API."""
    assert "processing:list_bright_objects" in RPC_METHODS
    assert "processing:set_bright_object" in RPC_METHODS
    assert rpc_registry._handlers["processing:list_bright_objects"] == (
        "astrometrics.processing",
        "list_preview_bright_objects",
    )
    assert rpc_registry._handlers["processing:set_bright_object"] == (
        "astrometrics.processing",
        "set_preview_bright_object",
    )


def test_the_list_comes_back_as_plain_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The objects and message are plain data; the call gets its inputs."""
    choices = BrightObjectChoices(
        target_id="M 52",
        objects=[
            BrightObjectChoice(
                simbad_id="NGC  7635",
                object_type="HII",
                category="nebula",
                ra_deg=350.2,
                dec_deg=61.2,
                major_axis_arcmin=9.0,
            )
        ],
    )
    listing = MagicMock(return_value=choices)
    monkeypatch.setattr(container.astrometrics.processing, "list_preview_bright_objects", listing)

    reply = asyncio.run(
        rpc_registry.execute("processing:list_bright_objects", {"target": "M 52", "refresh": True})
    )

    listing.assert_called_once_with(target="M 52", refresh=True)
    plain = serialize_rpc_result(reply)
    assert plain["objects"][0]["simbad_id"] == "NGC  7635"
    assert plain["message"] is None


def test_the_choice_reaches_the_library_and_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The save call passes the object's fields and returns the choice."""
    saving = MagicMock(
        return_value=PreviewBrightObject(
            mode="object", simbad_id="NGC  7635", ra_deg=350.2, dec_deg=61.2, major_axis_arcmin=9.0
        )
    )
    monkeypatch.setattr(container.astrometrics.processing, "set_preview_bright_object", saving)

    reply = asyncio.run(
        rpc_registry.execute(
            "processing:set_bright_object",
            {
                "target": "M 52",
                "mode": "object",
                "simbad_id": "NGC  7635",
                "ra_deg": 350.2,
                "dec_deg": 61.2,
                "major_axis_arcmin": 9.0,
            },
        )
    )

    saving.assert_called_once_with(
        target="M 52",
        mode="object",
        simbad_id="NGC  7635",
        ra_deg=350.2,
        dec_deg=61.2,
        major_axis_arcmin=9.0,
    )
    plain = serialize_rpc_result(reply)
    assert plain["mode"] == "object"
    assert plain["simbadId"] == "NGC  7635"


@pytest.mark.anyio
async def test_a_bad_choice_is_an_invalid_argument_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """``InvalidArgumentError`` reaches the caller with its code."""

    def refuse(target: str, mode: str = "auto", **_: object) -> None:
        """Raise the error the library raises for a bad choice.

        Raises
        ------
        InvalidArgumentError
            Always.
        """
        raise InvalidArgumentError("Mode 'object' needs simbad_id, ra_deg and dec_deg.")

    monkeypatch.setattr(container.astrometrics.processing, "set_preview_bright_object", refuse)
    response = await rpc_router.handle_rpc(
        RPCRequest(method="processing:set_bright_object", id=1, params={"target": "M 52", "mode": "object"})
    )
    assert json.loads(bytes(response.body))["error"]["data"]["code"] == "invalid_argument"
