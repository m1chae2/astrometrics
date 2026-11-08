"""Purpose: Deliver real frames for targets the user has already imaged.

Description: The camera simulator draws a frame that does not look like the
real sky. When the mount points at a target the user has real light frames
of, taken with the same camera, filter and capture setting, the operator
(`exposure_driver`) asks this module for a real frame instead. The frame
reaches the simulated library through the app's normal download, so the
stacking, measuring and review code runs on real data.

How a delivery works:

1. The real frames of the target are listed once through the running
   backend (``target:get_files``, then ``images:get_fits_header`` for each
   file). The library files and databases are never opened here, because the
   backend owns them while it runs.
2. The frames that match the simulated capture are put in the order they
   were taken. A cursor per target and setting gives the next one, and starts
   again at the first when all were used. When some frames have the
   requested exposure, only those are used.
3. The chosen frame is copied to a private cache folder with
   `night_replay.copy_frame` (a real copy, never a link, refused inside the
   user's real folders). Then the backend's ``images:delete`` removes the
   library frame, so the library never holds two copies of it. In the
   simulator's overlay library this writes a whiteout and leaves the real
   file as it was. The backend also forgets the frame's measurements.
4. The delivered file is a copy of the cached frame with new times:
   ``DATE-OBS``, ``DATE-END`` and ``MJD-OBS`` are the simulated time, as if
   the frame were taken now. The original values go to ``ODATEOBS``,
   ``ODATEEND`` and ``OMJDOBS``. ``AIRMASS``, ``OBJCTALT``, ``OBJCTAZ`` and
   ``OBJCTHA`` move to ``OAIRMASS``, ``OOBJALT``, ``OOBJAZ`` and ``OOBJHA``,
   so the app works them out again for the simulated sky. ``ORIGIN`` keeps
   the real frame's own value (or stays absent), and a keyword
   ``SIMDELIV = T`` says the simulator delivered the frame. The frame is
   real, so it does not get the ``SYNTHETC`` keyword that marks drawn frames.

The cache also lets a frame be delivered again after the cursor wraps
around, when the library no longer has the original.

Safety: the real files are only read. Every output goes in the folders the
caller names (the simulator's stand-in folders), and `night_replay.copy_frame`
refuses a destination inside or around the user's real folders.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from astropy.io import fits
from astropy.time import Time

from astrometricslib import FilterType
from simulator.device_settings import GUIDE_CAMERA, MAIN_CAMERA

LOGGER = logging.getLogger(__name__)

__all__ = [
    "DELIVERED_KEYWORD",
    "CaptureSetting",
    "FrameSource",
    "RealFrame",
    "read_origin",
    "restore_origin",
    "retime_header",
    "simulated_capture_setting",
]

#: Marks a frame the simulator delivered. It is not ``SYNTHETC``.
DELIVERED_KEYWORD = "SIMDELIV"
#: Marks a frame a camera simulator drew (simulator/README.md, "Design rules").
SYNTHETIC_KEYWORD = "SYNTHETC"
#: The ``INSTRUME`` names of the camera simulators. Frames they drew before
#: `SYNTHETIC_KEYWORD` existed carry only this name.
SIMULATOR_CAMERAS = frozenset({MAIN_CAMERA, GUIDE_CAMERA})
DELIVERED_COMMENT = "real frame delivered as if taken now"
#: Header keywords whose original values are kept under a short name.
TIME_KEYWORDS = {"DATE-OBS": "ODATEOBS", "DATE-END": "ODATEEND", "MJD-OBS": "OMJDOBS"}
#: Header keywords the app works out again, moved to original-value names.
MOVED_KEYWORDS = {"AIRMASS": "OAIRMASS", "OBJCTALT": "OOBJALT", "OBJCTAZ": "OOBJAZ", "OBJCTHA": "OOBJHA"}
#: Two exposures this close (seconds) count as the same exposure.
EXPOSURE_TOLERANCE_SECONDS = 1e-3
#: Two gains or offsets this close count as the same setting.
SETTING_TOLERANCE = 1e-6

#: The signature of the backend call: ``call(method, params) -> result``.
RpcCall = Callable[[str, dict], object]


@dataclass(frozen=True)
class CaptureSetting:
    """The setting of one capture, simulated or real.

    Parameters
    ----------
    camera : `str` or `None`
        The camera name (``INSTRUME``), or `None` when not known.
    filter_name : `str` or `None`
        The filter name; `None` or empty means no filter.
    gain, offset : `float` or `None`
        The camera gain and offset settings.
    binning : `int`
        The binning factor (``XBINNING``).
    exposure : `float` or `None`
        The exposure in seconds.
    """

    camera: str | None
    filter_name: str | None
    gain: float | None
    offset: float | None
    binning: int
    exposure: float | None


@dataclass(frozen=True)
class RealFrame:
    """One real light frame of the library.

    Parameters
    ----------
    path : `str`
        Where the library keeps the file.
    taken : `str`
        The ``DATE-OBS`` text, used to put frames in the order they were taken.
    setting : `CaptureSetting`
        The frame's capture setting.
    """

    path: str
    taken: str
    setting: CaptureSetting


def _normalized(text: str | None) -> str:
    """Reduce a name to lower-case letters and digits for comparing.

    Parameters
    ----------
    text : `str` or `None`
        A camera or filter name.

    Returns
    -------
    normalized : `str`
        The name without spaces or punctuation; ``"none"`` becomes empty.
    """
    reduced = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    return "" if reduced == "none" else reduced


def _filter_key(name: str | None) -> str:
    """Reduce a filter name to the library's filter, for comparing.

    The filter wheel and the capture program may spell one filter two ways,
    such as ``L`` and ``Luminance``. Both are the library's `FilterType.L`.

    Parameters
    ----------
    name : `str` or `None`
        A filter name.

    Returns
    -------
    key : `str`
        The `_normalized` value of the library filter the name stands for
        (by its short name or its full name), or of the name itself when it
        is none of them.
    """
    reduced = _normalized(name)
    for member in FilterType:
        if reduced in (_normalized(member.name), _normalized(member.value)):
            return _normalized(member.value)
    return reduced


def _number(value: object) -> float | None:
    """Read a header value as a number.

    Parameters
    ----------
    value : `object`
        A header value or its text.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when there is none.
    """
    try:
        return float(str(value).strip().strip("'"))
    except ValueError:
        return None


def _close(first: float | None, second: float | None) -> bool:
    """Say whether two optional settings are the same.

    Parameters
    ----------
    first, second : `float` or `None`
        The settings. `None` means the frame does not give it.

    Returns
    -------
    same : `bool`
        `True` when both are `None` or both are numbers within the tolerance.
    """
    if first is None or second is None:
        return first is None and second is None
    return abs(first - second) <= SETTING_TOLERANCE


def setting_from_header(header: dict[str, object]) -> CaptureSetting:
    """Read the capture setting out of a frame's header values.

    Parameters
    ----------
    header : `dict` [`str`, `object`]
        Keyword to value (the values may be text).

    Returns
    -------
    setting : `CaptureSetting`
        The setting. A missing binning is 1.
    """
    binning = _number(header.get("XBINNING"))
    exposure = _number(header.get("EXPTIME", header.get("EXPOSURE")))
    camera = header.get("INSTRUME", header.get("CAMERA"))
    filter_name = header.get("FILTER")
    return CaptureSetting(
        camera=None if camera is None else str(camera).strip().strip("'").strip(),
        filter_name=None if filter_name is None else str(filter_name).strip().strip("'").strip(),
        gain=_number(header.get("GAIN")),
        offset=_number(header.get("OFFSET")),
        binning=int(binning) if binning else 1,
        exposure=exposure,
    )


def simulated_capture_setting(
    path: Path, offset: float | None, filter_name: str | None = None
) -> tuple[CaptureSetting, datetime | None]:
    """Read the setting and start time of a frame the camera simulator drew.

    Parameters
    ----------
    path : `pathlib.Path`
        The simulator's frame.
    offset : `float` or `None`
        The camera offset the operator set (the simulator writes none).
    filter_name : `str`, optional
        The filter wheel's current filter, used when the header names none.

    Returns
    -------
    setting, started : `CaptureSetting`, `datetime.datetime` or `None`
        The setting and the frame's ``DATE-OBS`` (UTC), or `None` without one.
    """
    with fits.open(path, memmap=False) as hdus:
        header = {key: hdus[0].header[key] for key in hdus[0].header if key}
    setting = setting_from_header(header)
    started = None
    try:
        started = datetime.fromisoformat(str(header["DATE-OBS"])).replace(tzinfo=UTC)
    except KeyError, ValueError:
        started = None
    return (
        CaptureSetting(
            camera=setting.camera,
            filter_name=setting.filter_name or filter_name,
            gain=setting.gain,
            offset=setting.offset if setting.offset is not None else offset,
            binning=setting.binning,
            exposure=setting.exposure,
        ),
        started,
    )


def settings_match(simulated: CaptureSetting, real: CaptureSetting, camera: str | None) -> bool:
    """Say whether a real frame was taken with the simulated capture setting.

    Parameters
    ----------
    simulated : `CaptureSetting`
        The setting of the frame the simulator drew.
    real : `CaptureSetting`
        The setting of the real frame.
    camera : `str` or `None`
        The real camera the simulator stands for; `None` skips the camera test.
        The real frame matches when its camera name contains this name, or the
        other way round, ignoring case and punctuation.

    Returns
    -------
    matches : `bool`
        `True` when camera, filter, gain, offset and binning all agree. The
        exposure is not compared here (see `FrameSource`).
    """
    if camera:
        wanted, found = _normalized(camera), _normalized(real.camera)
        if not found or (wanted not in found and found not in wanted):
            return False
    return (
        _filter_key(simulated.filter_name) == _filter_key(real.filter_name)
        and _close(simulated.gain, real.gain)
        and _close(simulated.offset, real.offset)
        and simulated.binning == real.binning
    )


def read_origin(path: Path) -> tuple[str, str] | None:
    """Read the ``ORIGIN`` keyword of a frame, with its comment.

    Parameters
    ----------
    path : `pathlib.Path`
        The frame (read only).

    Returns
    -------
    origin : `tuple` [`str`, `str`] or `None`
        ``(value, comment)``, or `None` when the header has no ``ORIGIN``.
    """
    with fits.open(path, memmap=False) as hdus:
        header = hdus[0].header
        if "ORIGIN" not in header:
            return None
        return str(header["ORIGIN"]), header.comments["ORIGIN"]


def restore_origin(path: Path, origin: tuple[str, str] | None) -> None:
    """Put a frame's own ``ORIGIN`` back after `night_replay.copy_frame`.

    ``copy_frame`` stamps ``ORIGIN = ASTROMETRICS-SIMULATOR`` on its copy. A
    delivered frame is real data and keeps its own ``ORIGIN`` (the simulator
    leaves that keyword alone and marks the frame with ``SIMDELIV`` instead).

    Parameters
    ----------
    path : `pathlib.Path`
        The copy, changed in place.
    origin : `tuple` [`str`, `str`] or `None`
        What `read_origin` found in the real frame. `None` removes the
        keyword, because the real frame had none.
    """
    with fits.open(path, mode="update", memmap=False) as hdus:
        header = hdus[0].header
        if origin is None:
            header.remove("ORIGIN", ignore_missing=True)
        else:
            header["ORIGIN"] = origin
        hdus.flush()


def retime_header(header: fits.Header, started: datetime, exposure: float | None) -> None:
    """Give a real frame's header the simulated time and keep the originals.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The delivered copy's header, changed in place.
    started : `datetime.datetime`
        The simulated start of the exposure (UTC).
    exposure : `float` or `None`
        The exposure in seconds, for ``DATE-END``. `None` uses ``EXPTIME``.
    """
    seconds = exposure if exposure is not None else _number(header.get("EXPTIME"))
    for keyword, original in TIME_KEYWORDS.items():
        if keyword in header:
            header[original] = (header[keyword], "real frame's original")
    for keyword, original in MOVED_KEYWORDS.items():
        if keyword in header:
            header[original] = (header[keyword], "original; app recomputes")
            del header[keyword]
    start_text = started.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    header["DATE-OBS"] = (start_text, "simulated start (UTC)")
    if seconds is not None:
        end = started + timedelta(seconds=seconds)
        header["DATE-END"] = (
            end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "simulated end (UTC)",
        )
    header["MJD-OBS"] = (
        float(Time(started.astimezone(UTC).replace(tzinfo=None), scale="utc").mjd),
        "simulated start (MJD)",
    )
    header[DELIVERED_KEYWORD] = (True, DELIVERED_COMMENT)


class FrameSource:
    """Delivers the next real frame of a target in place of a simulated one.

    Parameters
    ----------
    call : `~collections.abc.Callable`
        ``call(method, params)`` runs one backend RPC method and returns its
        result (see `simulator.rpc_client.call_rpc`).
    cache_directory : `pathlib.Path`
        A private folder in the simulator's world, where each delivered
        frame's original copy is kept for later rounds.
    camera : `str`, optional
        The real camera the simulator stands for. `None` accepts any camera.
    clock : `~collections.abc.Callable`, optional
        Gives the simulated time, used when the simulator's frame has none.
    """

    def __init__(
        self,
        call: RpcCall,
        cache_directory: Path,
        camera: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Keep the settings; nothing is listed yet."""
        self.call = call
        self.cache_directory = cache_directory
        self.camera = camera
        self.clock = clock or (lambda: datetime.now(UTC))
        self._frames: dict[str, list[RealFrame]] = {}
        self._cursors: dict[tuple, int] = {}
        self._cached: dict[str, Path] = {}
        # The ORIGIN value each real frame had before `copy_frame` stamped it.
        self._origins: dict[str, tuple[str, str] | None] = {}

    def real_frames(self, target: str) -> list[RealFrame]:
        """List a target's real light frames in the order they were taken.

        The list is read once through the backend and kept, because delivered
        frames leave the library.

        Parameters
        ----------
        target : `str`
            The library target's id.

        Returns
        -------
        frames : `list` [`RealFrame`]
            The frames, earliest first, each with the library's camera name.
            Frames the simulator delivered earlier, frames a camera simulator
            drew, and files that are not light frames are left out.
        """
        if target in self._frames:
            return self._frames[target]
        frames: list[RealFrame] = []
        listing = self.call("target:get_files", {"target_id": target})
        for entry in (listing or {}).get("files", []) if isinstance(listing, dict) else []:
            path = str(entry.get("path", ""))
            cards = self.call("images:get_fits_header", {"path": path})
            header = {str(card["key"]): card["value"] for card in cards or [] if card.get("key")}
            if DELIVERED_KEYWORD in header or SYNTHETIC_KEYWORD in header:
                continue
            if str(header.get("INSTRUME", "")).strip("' ") in SIMULATOR_CAMERAS:
                # Drawn by a camera simulator in an earlier run of this world.
                continue
            image_type = str(header.get("IMAGETYP", "Light")).strip("' ").lower()
            if "light" not in image_type:
                continue
            setting = setting_from_header(header)
            if entry.get("camera"):
                # The library's own name for the camera (its folder), which
                # the settings use; INSTRUME is the driver's spelling.
                setting = replace(setting, camera=str(entry["camera"]))
            frames.append(RealFrame(path, str(header.get("DATE-OBS", entry.get("date", ""))), setting))
        frames.sort(key=lambda frame: (frame.taken, frame.path))
        self._frames[target] = frames
        return frames

    def next_frame(self, target: str, wanted: CaptureSetting) -> RealFrame | None:
        """Choose the next real frame for a capture, and move the cursor on.

        Parameters
        ----------
        target : `str`
            The library target's id.
        wanted : `CaptureSetting`
            The setting of the simulated capture.

        Returns
        -------
        frame : `RealFrame` or `None`
            The next frame in the order taken, wrapping to the first after the
            last. `None` when no frame has the same camera, filter and
            capture setting. Frames of the requested exposure are preferred.
        """
        matching = [
            frame for frame in self.real_frames(target) if settings_match(wanted, frame.setting, self.camera)
        ]
        if wanted.exposure is not None:
            same_exposure = [
                frame
                for frame in matching
                if frame.setting.exposure is not None
                and abs(frame.setting.exposure - wanted.exposure) <= EXPOSURE_TOLERANCE_SECONDS
            ]
            matching = same_exposure or matching
        if not matching:
            return None
        key = (
            target,
            _filter_key(wanted.filter_name),
            wanted.gain,
            wanted.offset,
            wanted.binning,
            tuple(frame.path for frame in matching),
        )
        position = self._cursors.get(key, 0)
        self._cursors[key] = position + 1
        return matching[position % len(matching)]

    def _original_copy(self, target: str, frame: RealFrame) -> Path:
        """Copy a real frame to the cache and take it out of the library.

        The first time, the frame is copied, then the backend deletes it, so
        the library has no second copy. Later rounds reuse the cached copy.

        Parameters
        ----------
        target : `str`
            The library target's id.
        frame : `RealFrame`
            The frame to deliver.

        Returns
        -------
        cached : `pathlib.Path`
            The cached copy of the real frame.

        Raises
        ------
        RuntimeError
            If the backend does not delete the frame. Nothing is delivered
            then. An unreadable real file raises `OSError`.
        """
        from simulator import night_replay

        if frame.path in self._cached:
            return self._cached[frame.path]
        folder = self.cache_directory / re.sub(r"[^A-Za-z0-9]+", "_", target)
        folder.mkdir(parents=True, exist_ok=True)
        cached = folder / f"{len(self._cached):05d}_{Path(frame.path).name}"
        original_origin = read_origin(Path(frame.path))
        night_replay.copy_frame(Path(frame.path), cached)
        restore_origin(cached, original_origin)
        outcome = self.call("images:delete", {"paths": [frame.path], "target_id": target})
        failed = outcome.get("failed") if isinstance(outcome, dict) else None
        if failed or not isinstance(outcome, dict) or frame.path not in outcome.get("deleted", []):
            cached.unlink(missing_ok=True)
            raise RuntimeError(f"The backend did not remove {frame.path}: {failed}")
        self._cached[frame.path] = cached
        self._origins[frame.path] = original_origin
        return cached

    def deliver(
        self,
        target: str,
        simulated_frame: Path,
        destination: Path,
        offset: float | None,
        filter_name: str | None,
    ) -> bool:
        """Replace a simulated frame with the next matching real frame.

        Parameters
        ----------
        target : `str`
            The library target the mount points at.
        simulated_frame : `pathlib.Path`
            The frame the camera simulator drew (read, not changed).
        destination : `pathlib.Path`
            Where the app will find the frame. Written in one step.
        offset : `float` or `None`
            The camera offset the operator set.
        filter_name : `str` or `None`
            The filter wheel's current filter.

        Returns
        -------
        delivered : `bool`
            `True` when `destination` now holds a real frame. `False` when no
            real frame matches or the backend could not be used; the caller
            then keeps the simulator's frame. Nothing is raised for a
            backend that is not reachable: that is a status.
        """
        from simulator import night_replay

        night_replay.refuse_real_folder(destination)
        wanted, started = simulated_capture_setting(simulated_frame, offset, filter_name)
        try:
            frame = self.next_frame(target, wanted)
            while frame is not None:
                try:
                    cached = self._original_copy(target, frame)
                    break
                except FileNotFoundError:
                    self._frames[target] = [
                        other for other in self._frames[target] if other.path != frame.path
                    ]
                    frame = self.next_frame(target, wanted)
            else:
                return False
        except (OSError, RuntimeError, ValueError) as error:
            LOGGER.warning("No real frame delivered for %s: %s", target, error)
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.delivering")
        night_replay.copy_frame(cached, temporary)
        restore_origin(temporary, self._origins.get(frame.path))
        try:
            with fits.open(temporary, mode="update", memmap=False) as hdus:
                retime_header(
                    hdus[0].header,
                    started or self.clock(),
                    frame.setting.exposure,
                )
                hdus.flush()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return True
