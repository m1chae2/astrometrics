"""Purpose: Unit tests for CameraController's guide-image retrieval.

Description: Verifies `get_last_image` extracts plain `bytes` out of the
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

    def __init__(self, data: bytes) -> None:
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

    def __init__(self, blobs: dict) -> None:
        self._blobs = blobs

    def getBLOB(self, name: str) -> _FakeBlobVector | None:  # ruff: ignore[invalid-function-name]
        """Return the named BLOB vector, or `None` if not present.

        Returns
        -------
        vector : `_FakeBlobVector` or `None`
            The fake vector registered for `name`, if any.
        """
        return self._blobs.get(name)


def test_get_last_image_returns_plain_bytes_from_ccd1() -> None:
    """A CCD1 frame comes back as `bytes`, not the raw BLOB element/vector."""
    frame = b"\xff\xd8 fake fits bytes"
    device = _FakeDevice({"CCD1": _FakeBlobVector([_FakeBlobElement(frame)])})
    controller = CameraController(client=None)

    result = controller.get_last_image(device)

    assert result == frame
    assert isinstance(result, bytes)


def test_get_last_image_falls_back_to_ccd2_when_ccd1_absent() -> None:
    """CCD2 is used when the device has no CCD1 BLOB."""
    frame = b"second camera frame"
    device = _FakeDevice({"CCD2": _FakeBlobVector([_FakeBlobElement(frame)])})
    controller = CameraController(client=None)

    assert controller.get_last_image(device) == frame


def test_get_last_image_returns_none_with_no_device() -> None:
    """A missing device returns None rather than raising."""
    controller = CameraController(client=None)

    assert controller.get_last_image(None) is None


def test_get_last_image_returns_none_with_no_blob() -> None:
    """A device with neither CCD1 nor CCD2 registered returns None."""
    device = _FakeDevice({})
    controller = CameraController(client=None)

    assert controller.get_last_image(device) is None


class _FakeSwitchElement:
    """A fake INDI switch element with a settable state."""

    def __init__(self) -> None:
        self.s = 0


class _FakeAbortDevice:
    """A fake INDI camera with a ``CCD_ABORT_EXPOSURE`` switch."""

    def __init__(self, has_switch: bool = True) -> None:
        self.switch = [_FakeSwitchElement()] if has_switch else None

    def getSwitch(self, name: str) -> list | None:  # ruff: ignore[invalid-function-name]
        """Return the abort switch for its INDI name.

        Returns
        -------
        switch : `list` or `None`
            The switch vector, or `None` for any other name or no switch.
        """
        return self.switch if name == "CCD_ABORT_EXPOSURE" else None


class _RecordingClient:
    """A fake INDI client that records the switches sent."""

    def __init__(self) -> None:
        self.sent: list = []

    def sendNewSwitch(self, switch: list) -> None:  # ruff: ignore[invalid-function-name]
        """Record a switch vector."""
        self.sent.append(switch)


def test_abort_turns_on_the_abort_switch_and_sends_it() -> None:
    """The camera's ``CCD_ABORT_EXPOSURE`` switch is turned on and sent."""
    client = _RecordingClient()
    device = _FakeAbortDevice()

    assert CameraController(client=client).abort(device) is True

    assert client.sent == [device.switch]
    assert device.switch[0].s == 1


def test_abort_sends_nothing_without_a_camera_or_a_switch() -> None:
    """No camera, or a camera with no abort switch, sends nothing."""
    client = _RecordingClient()
    controller = CameraController(client=client)

    assert controller.abort(None) is False
    assert controller.abort(_FakeAbortDevice(has_switch=False)) is False
    assert client.sent == []


class _FakeClient:
    """A fake INDI client that records BLOB modes and counts images."""

    def __init__(self) -> None:
        self.image_arrivals = 0
        self.modes: list[tuple[int, str, str]] = []

    def setBLOBMode(self, mode: int, device_name: str, property_name: str) -> None:  # ruff: ignore[invalid-function-name]
        """Record a BLOB mode request."""
        self.modes.append((mode, device_name, property_name))


class _NamedDevice(_FakeDevice):
    """A fake camera with a device name and a frame."""

    def getDeviceName(self) -> str:  # ruff: ignore[invalid-function-name]
        """Return the device name.

        Returns
        -------
        name : `str`
            The fake camera's name.
        """
        return "CCD Simulator"


def test_image_reception_sets_the_blob_mode_for_ccd1() -> None:
    """Turning reception on asks for BLOBs; off sets them to never."""
    from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi

    client = _FakeClient()
    controller = CameraController(client=client)
    device = _NamedDevice({})

    assert controller.set_image_reception(device, True) is True
    assert controller.set_image_reception(device, False) is True
    assert controller.set_image_reception(None, True) is False
    assert client.modes == [
        (PyIndi.B_ALSO, "CCD Simulator", "CCD1"),
        (PyIndi.B_NEVER, "CCD Simulator", "CCD1"),
    ]


def test_wait_for_new_image_ignores_a_frame_from_before_reception() -> None:
    """A frame held in CCD1 from before reception is not returned."""
    client = _FakeClient()
    client.image_arrivals = 3
    controller = CameraController(client=client)
    device = _NamedDevice({"CCD1": _FakeBlobVector([_FakeBlobElement(b"old")])})
    controller.set_image_reception(device, True)

    assert controller.wait_for_new_image(device, 0.2) is None


def test_wait_for_new_image_returns_the_frame_that_arrives() -> None:
    """A frame that arrives after reception was enabled is returned."""
    import threading

    client = _FakeClient()
    controller = CameraController(client=client)
    device = _NamedDevice({"CCD1": _FakeBlobVector([_FakeBlobElement(b"new")])})
    controller.set_image_reception(device, True)
    threading.Timer(0.15, lambda: setattr(client, "image_arrivals", 1)).start()

    assert controller.wait_for_new_image(device, 3.0) == b"new"
