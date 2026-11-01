"""Purpose: Test the calibration health RPC methods.

Description: ``calibration:assess`` hands the kind and the filters to the
library's calibration catalog and returns one health record for each set.
``calibration:measure_frames`` starts the measuring job through the image
processing service and answers with the job's id. The tests replace the
catalog's methods with recording stand-ins, call the methods through the RPC
registry, and check the arguments that reach the library and that the replies
are plain JSON.
"""

import asyncio
import json

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.rpc_protocol import serialize_rpc_result

ASSESS = "calibration:assess"
MEASURE = "calibration:measure_frames"


def test_both_methods_are_declared_and_served() -> None:
    """The methods are in the public interface and have a handler."""
    for method in (ASSESS, MEASURE):
        assert method in RPC_METHODS
        assert method in rpc_registry._handlers


def test_assess_passes_its_arguments_and_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kind and camera reach the library, and the reply is plain JSON."""
    asked: dict[str, object] = {}

    def fake_assess(**arguments: object) -> list[dict[str, object]]:
        """Record the arguments and give one made-up health record.

        Returns
        -------
        records : `list` [`dict`]
            One record, shaped like `CalibrationSetHealth`.
        """
        asked.update(arguments)
        return [{"rowKey": "dark|cam", "status": "good", "summary": "No problem found."}]

    monkeypatch.setattr(container.astrometrics.processing.calibration, "assess", fake_assess)

    result = serialize_rpc_result(
        asyncio.run(rpc_registry.execute(ASSESS, {"kind": "dark", "camera_id": "Cam"}))
    )

    assert asked == {"kind": "dark", "camera_id": "Cam"}
    assert result[0]["rowKey"] == "dark|cam"
    json.dumps(result)


def test_assess_needs_a_kind() -> None:
    """A call without a kind is refused."""
    with pytest.raises(InvalidArgumentError, match="kind"):
        asyncio.run(rpc_registry.execute(ASSESS, {"camera_id": "Cam"}))


def test_measure_frames_starts_the_job_and_answers_with_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job is run in the background, with the job list switched on."""
    asked: dict[str, object] = {}

    def fake_measure(**arguments: object) -> dict[str, object]:
        """Record the arguments and give a report that measured nothing.

        Returns
        -------
        report : `dict` [`str`, `object`]
            A report shaped like `CalibrationMeasurementReport`.
        """
        asked.update(arguments)
        return {"frameCount": 3, "measuredCount": 0, "unchangedCount": 3}

    monkeypatch.setattr(container.astrometrics.processing.calibration, "measure_frames", fake_measure)

    result = serialize_rpc_result(asyncio.run(rpc_registry.execute(MEASURE, {"kind": "bias", "force": True})))

    assert asked == {"kind": "bias", "camera_id": None, "force": True, "register_job": True}
    assert result["result"]["frameCount"] == 3
    json.dumps(result)
