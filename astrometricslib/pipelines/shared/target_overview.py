"""Purpose: Describe targets in short records instead of whole target objects.

Description: A target object carries every frame record, every stack summary
and every analysis result. For a target with a few hundred frames that is
more than a client can read in one reply. This module builds the short forms
the Targets list and the Image Processing screen show: a row per target
(name, position, frame counts, cameras, last night imaged, whether it has a
stack), and one target's record with its frames grouped by night, filter,
exposure and camera, and its quality summaries boiled down to their flags
and headline numbers.

Everything here only reads. Nothing is marked as changed, so a later save
never writes a target because it was looked at.
"""

import os
from datetime import UTC, datetime
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.utilities.observing_night import observing_night_id

MAXIMUM_FRAME_ROWS = 50
"""Most individual frame rows one target record can carry."""

QUALITY_LIST_LENGTH = 5
"""Longest list kept when a quality summary is boiled down."""

QUALITY_SECTIONS = ("astrometry", "photometry", "spectroscopy")
"""The analyses a target keeps a quality summary for."""


def _iso(timestamp: float | None) -> str | None:
    """Write a Unix time as an ISO 8601 UTC string.

    Returns
    -------
    text : `str` or `None`
        The time, or `None` if there is none.
    """
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if timestamp else None


def _exposure(frame: Any) -> float:
    """Read a frame's exposure in seconds.

    Returns
    -------
    seconds : `float`
        The exposure, or 0.0 if it cannot be read.
    """
    try:
        return float(frame.exposure)
    except TypeError, ValueError:
        return 0.0


def _stack_flag(stacking: Any) -> bool | None:
    """Say whether a stack's saved summary flagged a problem.

    Returns
    -------
    flagged : `bool` or `None`
        The flag, or `None` when the stack has no summary.
    """
    quality = getattr(stacking, "quality_summary", None)
    return None if quality is None else bool(quality.flagged)


def summarize_target(target: Any) -> dict[str, Any]:
    """Describe one target in a short row.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    row : `dict` [`str`, `Any`]
        Name, position, light frame counts, cameras and filters used, the
        first and last night imaged, total light exposure, and whether it has
        an imaging stack, a spectral stack and a processed picture.
    """
    lights = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
    spectral = [frame for frame in lights if frame_is_spectral(frame)]
    stamps = [frame.timestamp for frame in lights if frame.timestamp]
    cameras: dict[str, int] = {}
    filters: dict[str, int] = {}
    for frame in lights:
        cameras[frame.camera] = cameras.get(frame.camera, 0) + 1
        label = str(getattr(frame.filter, "value", frame.filter))
        filters[label] = filters.get(label, 0) + 1
    stack = target.stacking
    return {
        "id": target.id,
        "common_name": target.common_name or None,
        "ra": target.ra,
        "dec": target.dec,
        "light_frames": len(lights),
        "spectral_light_frames": len(spectral),
        "other_frames": len(target.frames) - len(lights),
        "cameras": cameras,
        "filters": filters,
        "first_frame": _iso(min(stamps)) if stamps else None,
        "last_frame": _iso(max(stamps)) if stamps else None,
        "total_light_exposure_hours": round(sum(_exposure(frame) for frame in lights) / 3600.0, 2),
        "has_stack": bool(stack.stacked_image),
        "has_spectral_stack": bool(target.spectral_stacking.stacked_image),
        "has_processed_picture": bool(stack.processed_image),
        "stack_flagged": _stack_flag(stack),
    }


def group_frames(frames: list[Any]) -> list[dict[str, Any]]:
    """Group frames by night, role, filter, exposure and camera.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The target's frames.

    Returns
    -------
    groups : `list` [`dict`]
        One entry per group, oldest night first, with the frame count, the
        first and last file names, and the pier sides seen.
    """
    groups: dict[tuple, dict[str, Any]] = {}
    for frame in frames:
        key = (
            observing_night_id(frame.timestamp) if frame.timestamp else "unknown",
            str(frame.role).upper(),
            str(getattr(frame.filter, "value", frame.filter)),
            round(_exposure(frame), 3),
            frame.camera,
        )
        entry = groups.setdefault(
            key,
            {
                "night": key[0],
                "role": key[1],
                "filter": key[2],
                "exposure_seconds": key[3],
                "camera": key[4],
                "frames": 0,
                "files": [],
                "pier_sides": set(),
            },
        )
        entry["frames"] += 1
        entry["files"].append(os.path.basename(frame.path))
        if frame.pier_side:
            entry["pier_sides"].add(frame.pier_side)
    rows = []
    for entry in groups.values():
        files = sorted(entry.pop("files"))
        entry["first_file"], entry["last_file"] = files[0], files[-1]
        entry["pier_sides"] = sorted(entry["pier_sides"])
        rows.append(entry)
    return sorted(
        rows, key=lambda row: (str(row["night"]), row["role"], row["filter"], row["exposure_seconds"])
    )


def condense_quality(summary: Any) -> dict[str, Any] | None:
    """Boil a quality summary down to its flags and headline numbers.

    The full summary can hold long lists (every frame, every candidate).
    This keeps single values and short lists, and reports how long each
    longer list was.

    Parameters
    ----------
    summary : `Any`
        A quality summary model, or `None`.

    Returns
    -------
    condensed : `dict` [`str`, `Any`] or `None`
        The flat values, short lists, and ``<name>_count`` for long ones.
    """
    if summary is None:
        return None
    dump = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
    condensed: dict[str, Any] = {}
    for key, value in dump.items():
        if isinstance(value, list):
            if len(value) <= QUALITY_LIST_LENGTH and all(not isinstance(item, dict | list) for item in value):
                condensed[key] = value
            else:
                condensed[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            flat = {name: item for name, item in value.items() if not isinstance(item, dict | list)}
            if flat:
                condensed[key] = flat
        else:
            condensed[key] = value
    return condensed


def describe_target(target: Any, include_frames: int = 0) -> dict[str, Any]:
    """Describe one target in a record a client can read in one reply.

    Parameters
    ----------
    target : `Target`
        The target.
    include_frames : `int`, optional
        How many individual frame rows (the newest light frames) to add, up
        to `MAXIMUM_FRAME_ROWS`. Zero by default: the grouped counts are
        usually what is wanted.

    Returns
    -------
    record : `dict` [`str`, `Any`]
        The summary row, the grouped frames, both stacks (paths and
        flags), the condensed quality summaries, the moving-object
        candidate count and, if asked for, the newest frame rows.
    """
    record = summarize_target(target)
    record["field_of_view"] = target.field_of_view or None
    record["main_camera"] = target.main_camera or None
    record["main_scope"] = target.main_scope or None
    record["frame_groups"] = group_frames(target.frames)
    record["stacks"] = {
        "imaging": {
            "stacked_image": target.stacking.stacked_image or None,
            "processed_image": target.stacking.processed_image or None,
            "configurations": sorted(target.stacking.stacks_by_configuration),
            "summary": condense_quality(target.stacking.quality_summary),
        },
        "spectral": {
            "stacked_image": target.spectral_stacking.stacked_image or None,
            "summary": condense_quality(target.spectral_stacking.quality_summary),
        },
    }
    record["quality"] = {name: condense_quality(getattr(target.quality, name)) for name in QUALITY_SECTIONS}
    asteroids = target.asteroid_detection
    record["moving_object_candidates"] = len(asteroids.candidates)
    record["moving_object_summary"] = condense_quality(asteroids.quality_summary)
    wanted = max(0, min(int(include_frames), MAXIMUM_FRAME_ROWS))
    if wanted:
        newest = sorted(
            (frame for frame in target.frames if str(frame.role).upper() == "LIGHT"),
            key=lambda frame: frame.timestamp or 0.0,
        )[-wanted:]
        record["newest_frames"] = [
            {
                "file": os.path.basename(frame.path),
                "taken_at": _iso(frame.timestamp),
                "filter": str(getattr(frame.filter, "value", frame.filter)),
                "exposure_seconds": _exposure(frame),
                "camera": frame.camera,
                "pier_side": frame.pier_side,
                "airmass": frame.airmass,
                "sensor_temperature_c": frame.sensor_temperature_c,
                "measured": {
                    key: value
                    for key, value in (
                        frame.measurements.model_dump(mode="json") if frame.measurements else {}
                    ).items()
                    if value is not None
                },
            }
            for frame in newest
        ]
    return record


def angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Give the angle between two sky positions, in degrees.

    Parameters
    ----------
    ra1, dec1, ra2, dec2 : `float`
        The two positions, in degrees.

    Returns
    -------
    separation : `float`
        The great-circle angle between them.
    """
    import math

    first, second = math.radians(dec1), math.radians(dec2)
    delta = math.radians(ra1 - ra2)
    cosine = math.sin(first) * math.sin(second) + math.cos(first) * math.cos(second) * math.cos(delta)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def count_targets_per_night(targets: list[Any]) -> dict[str, int]:
    """Count, for each observing night, how many targets have frames from it.

    Parameters
    ----------
    targets : `list` [`Target`]
        The targets to count.

    Returns
    -------
    nights : `dict` [`str`, `int`]
        Night id (``YYYY-MM-DD``, the evening's date) to target count, in
        date order.
    """
    counts: dict[str, int] = {}
    for target in targets:
        timestamps = [frame.timestamp for frame in target.frames if frame.timestamp]
        for night in {observing_night_id(stamp) for stamp in timestamps}:
            counts[night] = counts.get(night, 0) + 1
    return dict(sorted(counts.items()))


def find_target_loosely(targets: list[Any], target_id: str) -> Any | None:
    """Find one target by id, exactly first, then as part of an id.

    Parameters
    ----------
    targets : `list` [`Target`]
        The targets to search.
    target_id : `str`
        The id, matched ignoring case.

    Returns
    -------
    target : `Target` or `None`
        The first match, or `None`.
    """
    wanted = target_id.lower()
    exact = next((item for item in targets if item.id.lower() == wanted), None)
    return exact or next((item for item in targets if wanted in item.id.lower()), None)


def summary_rows(
    targets: list[Any],
    target_id: str | None,
    text: str | None,
    camera_id: str | None,
    region: tuple[float, float, float] | None,
    include_empty: bool,
) -> list[dict[str, Any]]:
    """Build the summary rows of the targets that pass every filter.

    Parameters
    ----------
    targets : `list` [`Target`]
        The targets to describe.
    target_id : `str` or `None`
        Keep targets whose id contains this text, ignoring case.
    text : `str` or `None`
        Keep targets whose id or common name contains this text.
    camera_id : `str` or `None`
        Keep targets with light frames from a camera whose name contains
        this text.
    region : `tuple` [`float`, `float`, `float`] or `None`
        Right ascension, declination and radius of a circle on the sky, in
        degrees. Rows inside it get ``separation_deg``.
    include_empty : `bool`
        Also keep targets with no frames.

    Returns
    -------
    rows : `list` [`dict`]
        One `summarize_target` row per kept target, in catalog order.
    """
    from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

    rows = []
    for target in targets:
        if target_id and target_id.lower() not in target.id.lower():
            continue
        if text and text.lower() not in f"{target.id} {target.common_name}".lower():
            continue
        if not include_empty and not target.frames:
            continue
        row = summarize_target(target)
        if camera_id and not any(camera_id.lower() in name.lower() for name in row["cameras"]):
            continue
        if region is not None:
            ra_deg, dec_deg, radius_deg = region
            try:
                separation = angular_separation_deg(
                    ra_deg,
                    dec_deg,
                    parse_coordinate_string(target.ra, True),
                    parse_coordinate_string(target.dec, False),
                )
            except InvalidArgumentError:
                continue
            if separation > radius_deg:
                continue
            row["separation_deg"] = round(separation, 3)
        rows.append(row)
    return rows
