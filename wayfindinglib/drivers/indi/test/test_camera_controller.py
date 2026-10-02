"""Purpose: Unit tests for CameraController's guide-image retrieval.

Description: Verifies `get_guide_image` extracts plain `bytes` out of the
INDI BLOB vector property rather than returning the raw (unpicklable) SWIG
object -- this is load-bearing now that the real `IndiInterface` runs in a
separate process (`backend/services/infrastructure/indi_worker.py`) and its
return values have to cross a `multiprocessing.Queue`, against a fake INDI
device, matching this codebase's established fake-device testing discipline
(`test_mount_controller.py`).
"""

from wayfindinglib.drivers.indi.camera_controller import CameraController


class _FakeBlobElement:
    """A fake INDI BLOB element holding raw frame bytes."""

    def __init__(self, data: bytes):  # ruff: ignore[missing-return-type-special-method]
        self._data = data

    def getblobdata(self) -> bytes:
        """Return this element's raw data.

        Returns
        -------
        data : `bytes`
            The frame bytes this fake element was built with.
        """
        return self._data


class _FakeBlobVector(list):
    """A fake INDI BLOB property vector: a list of one or more elements."""


class _FakeDevice:
    """A fake INDI device exposing only `getBLOB`."""

    def __init__(self, blobs: dict):  # ruff: ignore[missing-return-type-special-method]
        self._blobs = blobs

    def getBLOB(self, name: str):  # ruff: ignore[invalid-function-name, missing-return-type-private-function]
        """Return the named BLOB vector, or `None` if not present.

        Returns
        -------
        vector : `_FakeBlobVector` or `None`
            The fake vector registered for `name`, if any.
        """
        return self._blobs.get(name)


def test_get_guide_image_returns_plain_bytes_from_ccd1() -> None:
    """A CCD1 frame comes back as `bytes`, not the raw BLOB element/vector."""
    frame = b"\xff\xd8 fake fits bytes"
    device = _FakeDevice({"CCD1": _FakeBlobVector([_FakeBlobElement(frame)])})
    controller = CameraController(client=None)

    result = controller.get_guide_image(device)

    assert result == frame
    assert isinstance(result, bytes)


def test_get_guide_image_falls_back_to_ccd2_when_ccd1_absent() -> None:
    """CCD2 is used when the device has no CCD1 BLOB."""
    frame = b"second camera frame"
    device = _FakeDevice({"CCD2": _FakeBlobVector([_FakeBlobElement(frame)])})
    controller = CameraController(client=None)

    assert controller.get_guide_image(device) == frame


def test_get_guide_image_returns_none_with_no_device() -> None:
    """A missing device returns None rather than raising."""
    controller = CameraController(client=None)

    assert controller.get_guide_image(None) is None


def test_get_guide_image_returns_none_with_no_blob() -> None:
    """A device with neither CCD1 nor CCD2 registered returns None."""
    device = _FakeDevice({})
    controller = CameraController(client=None)

    assert controller.get_guide_image(device) is None
