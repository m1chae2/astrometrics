"""Purpose: Test the ``calibration:measure_sensor_noise`` RPC method.

Description: The method finds the camera the app names, builds a
`CaptureSetting` and hands both to the library's calibration catalog, which
measures the gain and read noise from the flat and bias frames. With
``save`` it calls the writing form, ``record_sensor_noise``. The tests
replace the catalog's methods with recording stand-ins, call the method
through the RPC registry, and check the arguments that reach the library and
that the reply is plain JSON.
"""

import asyncio
import json

import pytest

from astrometricslib import Camera, CaptureSetting
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.reports import MeasurementStatus
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services import subject_resolver
from backend.services.rpc_protocol import serialize_rpc_result

METHOD = "calibration:measure_sensor_noise"


def test_the_method_is_declared_and_served() -> None:
    """The method is in the public interface and has a handler."""
    assert METHOD in RPC_METHODS
    assert METHOD in rpc_registry._handlers


CAMERA = Camera(
    id="Test Camera", name="Test Camera", pixel_size_um=3.76, sensor_width_px=100, sensor_height_px=100
)
"""The camera the stand-in lookup finds."""


@pytest.mark.parametrize(("save", "method"), [(False, "measure_sensor_noise"), (True, "record_sensor_noise")])
def test_the_arguments_reach_the_library_and_the_reply_is_json(
    monkeypatch: pytest.MonkeyPatch, save: bool, method: str
) -> None:
    """The camera and setting reach the library; the reply is JSON."""
    asked: list[tuple[object, ...]] = []

    def fake_measure(*arguments: object) -> dict[str, object]:
        """Record the arguments and give a result that measured nothing.

        Returns
        -------
        result : `dict` [`str`, `object`]
            A ``not_measured`` result, shaped like `SensorNoiseMeasurement`.
        """
        asked.append(arguments)
        return {"status": MeasurementStatus.NOT_MEASURED.value, "reason": "no flats", "gainEPerAdu": None}

    monkeypatch.setattr(subject_resolver, "find_camera", lambda astrometrics, name: CAMERA)
    monkeypatch.setattr(container.astrometrics.calibration, method, fake_measure)

    parameters = {"camera_id": "test camera", "gain": "0", "offset": 10, "binning": "1x1", "save": save}
    result = serialize_rpc_result(asyncio.run(rpc_registry.execute(METHOD, parameters)))

    assert asked == [(CAMERA, CaptureSetting(camera="Test Camera", gain=0, offset=10, binning=1))]
    assert result["status"] == "not_measured"
    assert result["reason"] == "no flats"
    assert result["gainEPerAdu"] is None
    json.dumps(result)


def test_an_unknown_camera_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A camera no listed camera matches is refused with a message."""
    monkeypatch.setattr(subject_resolver, "find_camera", lambda astrometrics, name: None)
    with pytest.raises(NotFoundError, match="camera"):
        asyncio.run(rpc_registry.execute(METHOD, {"camera_id": "Nope", "gain": 0, "offset": 10}))


def test_a_missing_camera_is_an_error() -> None:
    """The camera is required, so a call without it is refused."""
    with pytest.raises(InvalidArgumentError, match="camera_id"):
        asyncio.run(rpc_registry.execute(METHOD, {"gain": 0, "offset": 10}))
