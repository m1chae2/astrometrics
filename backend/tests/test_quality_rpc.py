"""Purpose: Test the ``quality:query`` and ``quality:metrics`` RPC methods.

Description: Both methods read the library's quality root. The tests point
that root at a temporary library folder, call the methods through the RPC
registry, and check the replies are plain JSON values.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.rpc_protocol import serialize_rpc_result


@pytest.fixture
def library_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the quality root at a temporary library folder.

    Returns
    -------
    folder : `pathlib.Path`
        The folder, holding no database yet.
    """
    configuration = SimpleNamespace(get_library_path=lambda: tmp_path)
    monkeypatch.setattr(container.astrometrics.quality, "_config", configuration)
    return tmp_path


def test_the_methods_are_declared_and_served() -> None:
    """Both methods are in the public interface and have a handler."""
    for method in ("quality:query", "quality:metrics"):
        assert method in RPC_METHODS
        assert method in rpc_registry._handlers


def test_query_gives_an_empty_list_before_anything_is_stored(library_folder: Path) -> None:
    """No database yet: an empty list, and no file is made."""
    assert asyncio.run(rpc_registry.execute("quality:query", {})) == []
    assert list(library_folder.iterdir()) == []


def test_query_returns_measurements_as_json(library_folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A measurement comes back with camelCase keys and a text time."""
    stored = QualityMeasurement(
        metric="frame.star_width_moffat_arcsec",
        definition_version=1,
        subject=QualitySubject(level=SubjectLevel.FRAME, subject_id="M31/a.fits", target_id="M31"),
        status=MeasurementStatus.MEASURED,
        value=2.5,
        origin=MeasurementOrigin.MEASURED,
        run_id="run-1",
        measured_at=datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    asked: dict[str, object] = {}

    def fake_query(**arguments: object) -> list[QualityMeasurement]:
        """Record the arguments and give one measurement.

        Returns
        -------
        measurements : `list` [`QualityMeasurement`]
            The one stored measurement.
        """
        asked.update(arguments)
        return [stored]

    monkeypatch.setattr(container.astrometrics.quality, "query", fake_query)

    result = serialize_rpc_result(asyncio.run(rpc_registry.execute("quality:query", {"target_id": "M31"})))

    assert asked == {"target_id": "M31"}
    assert len(result) == 1
    assert result[0]["value"] == pytest.approx(2.5)
    assert result[0]["measuredAt"].startswith("2026-10-01T03:00")
    json.dumps(result)


def test_metrics_lists_the_declared_metrics_as_json() -> None:
    """Every declaration serializes to plain JSON values."""
    result = serialize_rpc_result(asyncio.run(rpc_registry.execute("quality:metrics", {})))

    assert result
    assert {"name", "unit", "checkpoint"} <= set(result[0])
    json.dumps(result)
