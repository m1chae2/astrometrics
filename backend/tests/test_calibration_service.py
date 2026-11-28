"""Purpose: Test that the calibration RPC replies keep their shapes.

Description: `CalibrationService` turns the app's ``calibration:query``
arguments into library calls and the library's rows back into the reply.
The library here is a real, empty calibration library whose counts are
replaced by fixed ones, so the tests check the reply fields: the counts
rows, the camera totals, the matched row keys and the frame groups of a
target.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration, FrameRecord, Target
from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.exceptions import InvalidArgumentError
from backend.services.processing.calibration_service import CalibrationService

STATS = {
    "darks": [
        {
            "camera": "ASI533",
            "gain": "100",
            "offset": 10.0,
            "binning": "1x1",
            "temperature_c": -9.0,
            "exposure_s": 60.0,
            "count": 20,
        },
    ],
    "biases": [{"camera": "ASI533", "gain": "100", "offset": 10.0, "binning": "2x2", "count": 30}],
    "flats": [
        {"camera": "ASI533", "filter": "L", "gain": "100", "offset": 10.0, "binning": "2x2", "count": 25}
    ],
}
"""The fixed counts of the stand-in index."""


@pytest.fixture
def service(tmp_path: Path) -> CalibrationService:
    """Make the service over a catalog whose index gives `STATS`.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Where the empty library saves.

    Returns
    -------
    service : `CalibrationService`
        The service.
    """
    catalog = CalibrationCatalog(AppConfiguration(), None)
    library = CalibrationLibrary(
        app_config=SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    )
    object.__setattr__(library, "get_stats", lambda: STATS)
    catalog._library = library
    target = Target(
        id="M 81",
        frames=[
            FrameRecord(path="a.fits", exposure_s=60.0, role="LIGHT", camera="ASI533", gain=100, offset=10),
            FrameRecord(path="b.fits", exposure_s=60.0, role="LIGHT", camera="ASI533", gain=100, offset=10),
        ],
    )
    astrometrics: Any = SimpleNamespace(
        calibration=catalog, targets=SimpleNamespace(get=lambda target_id, refresh=False: target)
    )
    return CalibrationService(astrometrics)


def test_counts_rows_carry_the_keys_the_counts_table_reads(service: CalibrationService) -> None:
    """Each kind's rows carry the keys the counts table reads; totals agree."""
    answer = service.query()

    assert answer.detail == "counts"
    assert answer.dark_temperature_tolerance_c == pytest.approx(3.0)
    assert list(answer.darks[0]) == [
        "camera",
        "gain",
        "offset",
        "binning",
        "temperature_c",
        "exposure_s",
        "count",
        "row_key",
    ]
    assert list(answer.biases[0]) == ["camera", "gain", "offset", "binning", "count", "row_key"]
    assert answer.biases[0]["gain"] == 100
    assert answer.flats[0]["filter"] == "L"
    assert answer.camera_totals == [
        {"camera": "ASI533", "dark_count": 20, "bias_count": 30, "flat_count": 25}
    ]
    assert service.query(kind="bias").darks is None


def test_match_gives_the_keys_of_the_rows_with_the_setting(service: CalibrationService) -> None:
    """Binning "2" and a whole-number gain find the 2x2 bias row."""
    answer = service.query(detail="match", kind="bias", camera_id="asi533", gain=100, binning="2")

    assert answer.matched_row_keys == [service.query().biases[0]["row_key"]]
    with pytest.raises(InvalidArgumentError):
        service.query(detail="match", gain=100)
    with pytest.raises(InvalidArgumentError):
        service.query(detail="counts", gain=100)


def test_target_details_keep_their_shapes(service: CalibrationService) -> None:
    """The frame groups and light counts keep the fields the list reads."""
    groups = service.query(detail="target_match", target="M 81").groups
    lights = service.query(detail="target_frames", target="M 81").lights

    assert set(groups[0]) == {
        "filter",
        "gain",
        "offset",
        "exposureS",
        "count",
        "darks",
        "camera",
        "focalLengthMm",
        "darkMatch",
        "flatMatch",
        "biasMatch",
        "binning",
    }
    assert (groups[0]["exposureS"], groups[0]["count"]) == (60.0, 2)
    assert set(groups[0]["darkMatch"]) == {"count", "fallbacks", "problem"}
    assert lights[0]["count"] == 2
    assert set(lights[0]) == {"telescope", "camera", "gain", "exposure_s", "filter", "count"}
