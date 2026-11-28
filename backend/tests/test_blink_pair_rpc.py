"""Purpose: Prove that the Blink button's RPC method is public and routed.

Description: `quality:choose_blink_pair` goes through the quality
measurement service to ``quality.frames.get_blink_partner``, and the answer
reaches the screen with the camel-case names the generated TypeScript type
uses.
"""

import asyncio

import pytest

from astrometricslib import Target
from astrometricslib.models.quality.blink_pair import BlinkPair
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.rpc_protocol import serialize_rpc_result


def test_the_method_is_declared_and_routed() -> None:
    """The method is public and goes to the quality measurement service."""
    assert "quality:choose_blink_pair" in RPC_METHODS
    assert rpc_registry._handlers["quality:choose_blink_pair"] == (
        "quality_measurement_service",
        "choose_blink_pair",
    )


def test_the_answer_reaches_the_screen_with_camel_case_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call reaches the library and the pair comes back by alias."""
    calls: list[tuple[str, str]] = []

    def get_blink_partner(target: Target, path: str) -> BlinkPair:
        """Record the call and give a pair.

        Returns
        -------
        pair : `BlinkPair`
            A found pair.
        """
        calls.append((target.id, path))
        return BlinkPair(found=True, path=path, typical_path="/f/typical.fits", star_x=10.0, star_y=20.0)

    monkeypatch.setattr(container.astrometrics.quality.frames, "get_blink_partner", get_blink_partner)
    monkeypatch.setattr(
        container.astrometrics.targets, "get", lambda target_id, *args, **kwargs: Target(id=target_id)
    )

    reply = asyncio.run(
        rpc_registry.execute("quality:choose_blink_pair", {"target": "M 81", "path": "/f/bad.fits"})
    )

    plain = serialize_rpc_result(reply)
    assert calls == [("M 81", "/f/bad.fits")]
    assert plain["typicalPath"] == "/f/typical.fits"
    assert plain["starX"] == pytest.approx(10.0)
