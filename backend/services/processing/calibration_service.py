"""Purpose: Serve the calibration RPC methods of the app's calibration panels.

Description: The app asks about the calibration library in the shapes its
panels were built for: ``calibration:query`` with a ``detail`` (the counts
table, the rows a capture setting matches, or a target's frames and the
darks that match them), ``calibration:assess`` with a kind and the setting
fields, and ``calibration:measure_sensor_noise`` with a camera name, a gain
and an offset. The library takes objects instead: a `CaptureSetting`, a
`Camera` and a list of frames (`astrometrics.calibration`). This service
turns the app's arguments into those objects and the library's answers back
into the replies the panels read, so the replies are the same as before the
library changed.

`CalibrationQueryResult` is the reply of ``calibration:query``. The app's
types are generated from it.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel

from astrometricslib import CaptureSetting
from astrometricslib.arguments import check_choice, reject_unused_arguments
from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.parameters import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C, resolve_parameter
from backend.services.subject_resolver import SubjectResolver

CALIBRATION_DETAILS = ("counts", "target_match", "target_frames", "match")
"""The answers ``calibration:query`` gives, chosen with ``detail``."""

_DETAIL_ARGUMENTS = {
    "counts": ("kind",),
    "target_match": ("target", "camera_id"),
    "target_frames": ("target",),
    "match": ("kind", "camera_id", "gain", "offset", "binning", "temperature_c"),
}
"""The arguments each detail uses. Any other argument is refused."""

_ASSESS_ARGUMENTS = {
    "dark": ("camera_id", "gain", "offset", "binning", "temperature_c", "exposure_s"),
    "bias": ("camera_id", "gain", "offset", "binning"),
    "flat": ("camera_id", "gain", "offset", "binning", "telescope", "filter_name"),
}
"""The arguments each kind of ``calibration:assess`` uses."""

_ROWS_KEY = {"dark": "darks", "bias": "biases", "flat": "flats"}
"""The reply field that holds each kind's rows."""


class CalibrationQueryResult(BaseModel):
    """The reply of ``calibration:query``.

    Attributes
    ----------
    detail : `str`
        The detail asked for.
    dark_temperature_tolerance_c : `float` or `None`
        How far, in degrees Celsius, a dark's sensor temperature may differ
        from a light frame's and still match.
    kind : `str` or `None`
        The kind asked for, or `None` for all.
    target_id : `str` or `None`
        The target, for the target details.
    darks, biases, flats : `list` [`dict`] or `None`
        ``counts``: one row per camera, setting and exposure (or filter).
    groups : `list` [`dict`] or `None`
        ``target_match``: the target's light frames grouped by filter, gain
        and exposure, each with the darks, biases and flats that match.
    lights : `list` [`dict`] or `None`
        ``target_frames``: the target's light frames counted by telescope,
        camera, gain, exposure and filter.
    camera_totals : `list` [`dict`] or `None`
        ``counts`` and ``match``: the frame totals of each camera.
    matched_row_keys : `list` [`str`] or `None`
        ``match``: the ``row_key`` of every counts row with the asked
        settings.
    """

    detail: str
    dark_temperature_tolerance_c: float | None = None
    kind: str | None = None
    target_id: str | None = None
    darks: list[dict[str, Any]] | None = None
    biases: list[dict[str, Any]] | None = None
    flats: list[dict[str, Any]] | None = None
    groups: list[dict[str, Any]] | None = None
    lights: list[dict[str, Any]] | None = None
    camera_totals: list[dict[str, Any]] | None = None
    matched_row_keys: list[str] | None = None


def _whole_number(name: str, value: Any) -> int | None:
    """Read a gain, offset or binning the app sent as a whole number.

    Parameters
    ----------
    name : `str`
        The argument's name, for the message.
    value : `Any`
        A number, text such as ``"100"``, a binning such as ``"2x2"``, or
        `None`.

    Returns
    -------
    number : `int` or `None`
        The whole number, or `None` when no value was sent.

    Raises
    ------
    InvalidArgumentError
        If the value is not a whole number.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip().lower()
    binned = re.fullmatch(r"(\d+)x(\d+)", text)
    if binned and binned.group(1) == binned.group(2):
        return int(binned.group(1))
    try:
        number = float(text)
    except ValueError:
        number = float("nan")
    if not number.is_integer():
        raise InvalidArgumentError(f"{name} must be a whole number, not {value!r}.", details={name: value})
    return int(number)


def _count_row(row: Any) -> dict[str, Any]:
    """Write one library counts row in the shape the counts table reads.

    Parameters
    ----------
    row : `CalibrationSetCount`
        The row from `calibration.query`.

    Returns
    -------
    row : `dict` [`str`, `Any`]
        ``camera``, ``gain``, ``offset``, ``binning``, then ``temperature_c``
        and ``exposure_s`` for a dark or ``filter`` for a flat, ``count`` and
        ``row_key``.
    """
    shaped: dict[str, Any] = {"camera": row.camera}
    if row.kind == "flat":
        shaped["filter"] = row.filter_name
    shaped.update({"gain": row.gain, "offset": row.offset, "binning": row.binning})
    if row.kind == "dark":
        shaped.update({"temperature_c": row.temperature_c, "exposure_s": row.exposure_s})
    shaped.update({"count": row.count, "row_key": row.row_key})
    return shaped


def _camera_totals(rows: Any) -> list[dict[str, Any]]:
    """Add up each camera's dark, bias and flat frames.

    Parameters
    ----------
    rows : `Iterable` [`CalibrationSetCount`]
        Every counts row of the library.

    Returns
    -------
    totals : `list` [`dict` [`str`, `Any`]]
        One entry per camera, sorted by name ignoring case.
    """
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = totals.setdefault(
            row.camera, {"camera": row.camera, "dark_count": 0, "bias_count": 0, "flat_count": 0}
        )
        entry[f"{row.kind}_count"] += row.count
    return sorted(totals.values(), key=lambda entry: str(entry["camera"]).casefold())


def _group_row(match: Any) -> dict[str, Any]:
    """Write one light-frame group in the shape the frame list reads.

    Parameters
    ----------
    match : `CalibrationMatch`
        The group from `calibration.match`.

    Returns
    -------
    group : `dict` [`str`, `Any`]
        The group's settings, its frame count, the dark count as text (or
        ``"Missing"``) and the ``darkMatch``, ``flatMatch`` and
        ``biasMatch`` records.
    """
    return {
        "filter": match.filter_name,
        "gain": match.gain,
        "offset": match.offset,
        "exposureS": match.exposure_s,
        "count": match.frame_count,
        "darks": f"{match.dark.count}" if match.dark.count else "Missing",
        "camera": match.camera,
        "focalLengthMm": match.focal_length_mm,
        "darkMatch": match.dark.model_dump(by_alias=True),
        "flatMatch": match.flat.model_dump(by_alias=True),
        "biasMatch": match.bias.model_dump(by_alias=True),
        "binning": match.binning,
    }


def light_frame_counts(target: Any) -> list[dict[str, Any]]:
    """Count a target's light frames by equipment, gain, exposure and filter.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    lights : `list` [`dict` [`str`, `Any`]]
        One entry per combination, in the order first seen.
    """
    counts: dict[tuple, int] = {}
    for frame in target.frames:
        if frame.role != "LIGHT":
            continue
        filter_text = frame.filter.value if hasattr(frame.filter, "value") else str(frame.filter)
        key = (frame.telescope, frame.camera, frame.gain, frame.exposure_s, filter_text)
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "telescope": telescope,
            "camera": camera,
            "gain": gain,
            "exposure_s": exposure_s,
            "filter": filter_text,
            "count": n,
        }
        for (telescope, camera, gain, exposure_s, filter_text), n in counts.items()
    ]


class CalibrationService:
    """Adapt the app's calibration RPC arguments and replies to the library.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The backend's shared library handle.
    """

    def __init__(self, astrometrics: Any) -> None:
        """Keep the library handle and the id resolver."""
        self._astrometrics = astrometrics
        self._subjects = SubjectResolver(astrometrics)

    @property
    def _calibration(self) -> Any:
        """The library's calibration sub-API.

        Returns
        -------
        calibration : `CalibrationCatalog`
            ``astrometrics.calibration``.
        """
        return self._astrometrics.calibration

    def query(
        self,
        kind: Literal["dark", "bias", "flat"] | None = None,
        detail: Literal["counts", "target_match", "target_frames", "match"] = "counts",
        target: str | None = None,
        camera_id: str | None = None,
        gain: float | None = None,
        offset: float | None = None,
        binning: str | int | None = None,
        temperature_c: float | None = None,
    ) -> CalibrationQueryResult:
        """Report on the calibration library. Nothing is written.

        Arguments used by each detail (any other argument is refused):

        - ``"counts"``: ``kind``.
        - ``"match"``: ``camera_id`` (needed), ``kind``, ``gain``,
          ``offset``, ``binning`` and ``temperature_c``.
        - ``"target_match"``: ``target`` (needed) and ``camera_id``.
        - ``"target_frames"``: ``target`` (needed).

        Parameters
        ----------
        kind : `str`, optional
            ``"dark"``, ``"bias"`` or ``"flat"``. Leave it out for all three.
        detail : `str`, optional
            ``"counts"`` (default), ``"match"``, ``"target_match"`` or
            ``"target_frames"``.
        target : `str`, optional
            The target's id, for the target details.
        camera_id : `str`, optional
            For ``"target_match"``, only frames from this camera. For
            ``"match"``, the camera to match (ignoring case).
        gain, offset, binning, temperature_c : optional
            For ``"match"``, the settings a row must have.

        Returns
        -------
        answer : `CalibrationQueryResult`
            The rows for the chosen detail.

        Raises
        ------
        InvalidArgumentError
            If the detail or kind is unknown, an argument the detail does
            not use is given, or a needed argument is missing.
        """
        check_choice("detail", detail, CALIBRATION_DETAILS)
        if kind is not None:
            check_choice("kind", kind, tuple(_ROWS_KEY))
        given = {
            "kind": kind is not None,
            "target": target is not None,
            "camera_id": camera_id is not None,
            "gain": gain is not None,
            "offset": offset is not None,
            "binning": binning is not None,
            "temperature_c": temperature_c is not None,
        }
        reject_unused_arguments(detail, _DETAIL_ARGUMENTS, given)
        tolerance_c = resolve_parameter(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value
        if detail in ("counts", "match"):
            every_row = list(self._calibration.query())
            totals = _camera_totals(every_row)
            if detail == "match":
                if camera_id is None:
                    raise InvalidArgumentError("detail='match' needs a camera_id.")
                setting = CaptureSetting(
                    camera=camera_id,
                    gain=_whole_number("gain", gain),
                    offset=_whole_number("offset", offset),
                    binning=_whole_number("binning", binning),
                    temperature_c=temperature_c,
                )
                matched = [row.row_key for row in self._calibration.query(kind, setting=setting)]
                return CalibrationQueryResult(
                    detail=detail,
                    kind=kind,
                    camera_totals=totals,
                    matched_row_keys=matched,
                    dark_temperature_tolerance_c=tolerance_c,
                )
            rows = {
                _ROWS_KEY[label]: [_count_row(row) for row in every_row if row.kind == label]
                for label in _ROWS_KEY
                if kind in (None, label)
            }
            return CalibrationQueryResult(
                detail=detail,
                kind=kind,
                camera_totals=totals,
                dark_temperature_tolerance_c=tolerance_c,
                **rows,
            )
        if target is None:
            raise InvalidArgumentError(f"detail={detail!r} needs a target.")
        resolved = self._subjects.target(target)
        if detail == "target_frames":
            return CalibrationQueryResult(
                detail=detail,
                target_id=resolved.id,
                lights=light_frame_counts(resolved),
                dark_temperature_tolerance_c=tolerance_c,
            )
        frames = [frame for frame in resolved.frames if not camera_id or frame.camera == camera_id]
        return CalibrationQueryResult(
            detail=detail,
            target_id=resolved.id,
            groups=[_group_row(match) for match in self._calibration.match(frames)],
            dark_temperature_tolerance_c=tolerance_c,
        )

    def frame_groups(self, target: Any, camera_id: str | None = None) -> list[dict[str, Any]]:
        """Group a target's light frames with the calibration that matches.

        Parameters
        ----------
        target : `Target`
            The target.
        camera_id : `str`, optional
            Only frames from this camera.

        Returns
        -------
        groups : `list` [`dict` [`str`, `Any`]]
            The groups, in the shape of the ``target_match`` detail.
        """
        frames = [frame for frame in target.frames if not camera_id or frame.camera == camera_id]
        return [_group_row(match) for match in self._calibration.match(frames)]

    def assess(
        self,
        kind: Literal["dark", "bias", "flat"],
        camera_id: str | None = None,
        gain: float | None = None,
        offset: float | None = None,
        binning: str | int | None = None,
        temperature_c: float | None = None,
        exposure_s: float | None = None,
        telescope: str | None = None,
        filter_name: str | None = None,
    ) -> list[Any]:
        """Judge the calibration sets of one kind. Nothing is written.

        Arguments used by each kind (any other argument is refused):

        - ``"dark"``: ``camera_id``, ``gain``, ``offset``, ``binning``,
          ``temperature_c`` and ``exposure_s``.
        - ``"bias"``: ``camera_id``, ``gain``, ``offset`` and ``binning``.
        - ``"flat"``: ``camera_id``, ``gain``, ``offset``, ``binning``,
          ``telescope`` and ``filter_name``.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The kind of calibration frame.
        camera_id : `str`, optional
            Only this camera's sets. The other settings need it.
        gain, offset, binning, temperature_c, exposure_s : optional
            Only sets with these settings.
        telescope, filter_name : `str`, optional
            Flats only: the telescope and the filter.

        Returns
        -------
        health : `list` [`CalibrationSetHealth`]
            One record per matching set.

        Raises
        ------
        InvalidArgumentError
            If the kind is unknown, an argument the kind does not use is
            given, or a setting is given without a camera.
        """
        check_choice("kind", kind, tuple(_ROWS_KEY))
        given = {
            "camera_id": camera_id is not None,
            "gain": gain is not None,
            "offset": offset is not None,
            "binning": binning is not None,
            "temperature_c": temperature_c is not None,
            "exposure_s": exposure_s is not None,
            "telescope": telescope is not None,
            "filter_name": filter_name is not None,
        }
        reject_unused_arguments(kind, _ASSESS_ARGUMENTS, given)
        if camera_id is None:
            if any(value for name, value in given.items() if name != "camera_id"):
                raise InvalidArgumentError("A setting to assess needs a camera_id.")
            return self._calibration.assess(kind)
        setting = CaptureSetting(
            camera=camera_id,
            gain=_whole_number("gain", gain),
            offset=_whole_number("offset", offset),
            binning=_whole_number("binning", binning),
            temperature_c=temperature_c,
            exposure_s=exposure_s,
            telescope=telescope,
            filter_name=filter_name,
        )
        return self._calibration.assess(kind, setting)

    def measure_sensor_noise(
        self,
        camera_id: str,
        gain: float,
        offset: float,
        binning: str = "1x1",
        temperature_c: float | None = None,
        exposure_s: float | None = None,
        telescope: str | None = None,
        filter_name: str | None = None,
        save: bool = False,
    ) -> Any:
        """Measure a camera setting's gain and read noise; save it if asked.

        Parameters
        ----------
        camera_id : `str`
            The camera, in any spelling of its name.
        gain : `float`
            The camera gain setting, a whole number.
        offset : `float`
            The camera offset, a whole number.
        binning : `str`, optional
            The binning, such as ``"1x1"``.
        temperature_c : `float`, optional
            Use only frames within 1 degree C of this sensor temperature.
        exposure_s : `float`, optional
            Use only flats of this exposure.
        telescope, filter_name : `str`, optional
            Use only flats taken through this telescope or filter.
        save : `bool`, optional
            Write a measured result into the camera profile, with a backup
            of the config first.

        Returns
        -------
        measurement : `SensorNoiseMeasurement`
            The measurement, or the reason nothing was measured.

        Notes
        -----
        Raises `NotFoundError` if no camera has that name.
        """
        camera = self._subjects.camera(camera_id)
        setting = CaptureSetting(
            camera=camera.name,
            gain=_whole_number("gain", gain),
            offset=_whole_number("offset", offset),
            binning=_whole_number("binning", binning),
            temperature_c=temperature_c,
            exposure_s=exposure_s,
            telescope=telescope,
            filter_name=filter_name,
        )
        if save:
            return self._calibration.record_sensor_noise(camera, setting)
        return self._calibration.measure_sensor_noise(camera, setting)
