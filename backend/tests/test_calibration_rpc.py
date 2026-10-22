"""Purpose: Test the ``calibration:measure_sensor_noise`` RPC method.

Description: The method hands a camera setting to the library's calibration
catalog, which measures the gain and read noise from the flat and bias
frames. The tests replace the catalog's method with a recording stand-in,
call the method through the RPC registry, and check the arguments that reach
the library and that the reply is plain JSON.
"""

import asyncio
import json

import pytest

from astrometricslib import InvalidArgumentError, MeasurementStatus
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.rpc_protocol import serialize_rpc_result

METHOD = "calibration:measure_sensor_noise"


def test_the_method_is_declared_and_served() -> None:
    """The method is in the public interface and has a handler."""
    assert METHOD in RPC_METHODS
    assert METHOD in rpc_registry._handlers


def test_the_arguments_reach_the_library_and_the_reply_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arguments pass through unchanged, and a refusal comes back as JSON."""
    asked: dict[str, object] = {}

    def fake_measure(**arguments: object) -> dict[str, object]:
        """Record the arguments and give a result that measured nothing.

        Returns
        -------
        result : `dict` [`str`, `object`]
            A ``not_measured`` result, shaped like `SensorNoiseMeasurement`.
        """
        asked.update(arguments)
        return {"status": MeasurementStatus.NOT_MEASURED.value, "reason": "no flats", "gainEPerAdu": None}

    monkeypatch.setattr(container.astrometrics.processing.calibration, "measure_sensor_noise", fake_measure)

    parameters = {"camera_id": "Test Camera", "gain": 0, "offset": 10, "binning": "1x1", "save": False}
    result = serialize_rpc_result(asyncio.run(rpc_registry.execute(METHOD, parameters)))

    assert asked == parameters
    assert result["status"] == "not_measured"
    assert result["reason"] == "no flats"
    assert result["gainEPerAdu"] is None
    json.dumps(result)


def test_a_missing_camera_is_an_error() -> None:
    """The camera is required, so a call without it is refused."""
    with pytest.raises(InvalidArgumentError, match="camera_id"):
        asyncio.run(rpc_registry.execute(METHOD, {"gain": 0, "offset": 10}))
