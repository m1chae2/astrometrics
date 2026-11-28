"""Purpose: List targets and count their frames, for `TargetCatalog`.

Description: The owners of `TargetCatalog.query` (`query_targets`: the
targets that pass the filters, as a page), `count_by_camera`
(`count_frames_by_camera`) and `count_by_night` (`count_nights`), with the
helpers they share (`angular_separation_deg`, `count_targets_per_night`).

Everything here only reads. Nothing is marked as changed, so a later save
never writes a target because it was looked at.
"""

from datetime import UTC, datetime
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.utilities.observing_night import observing_night_id


def _iso(timestamp: float | None) -> str | None:
    """Write a Unix time as an ISO 8601 UTC string.

    Returns
    -------
    text : `str` or `None`
        The time, or `None` if there is none.
    """
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if timestamp else None


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


TARGET_ORDERS = ("newest", "name", "separation")
"""The orders `query_targets` sorts by, besides the stored order."""


def query_targets(
    targets: list[Any],
    config: Any,
    *,
    text: str | None = None,
    camera: str | None = None,
    configuration: Any = None,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    radius_deg: float | None = None,
    object_type: str | None = None,
    order_by: str | None = None,
    include_empty: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> Any:
    """Pick the targets that pass every filter, as a page.

    Parameters
    ----------
    targets : `list` [`Target`]
        Every target, in stored order.
    config : `AppConfiguration`
        The settings, for the camera profiles.
    text : `str`, optional
        Keep targets whose id or common name contains this text, ignoring
        case.
    camera : `str`, optional
        Keep targets with light frames from a camera whose name contains
        this text, ignoring case.
    configuration : `ObservatoryConfiguration`, optional
        Keep targets with light frames taken with this equipment.
    ra_deg, dec_deg, radius_deg : `float`, optional
        A circle on the sky (ICRS, degrees): keep targets whose position is
        inside it. All three go together.
    object_type : `str`, optional
        Keep targets of this kind (a `TargetObjectType` value).
    order_by : `str`, optional
        ``"newest"`` (by the last light frame taken), ``"name"`` or
        ``"separation"`` (nearest the circle's center first). Left out,
        the stored order.
    include_empty : `bool`, optional
        Also keep targets with no frames.
    limit : `int` or `None`, optional
        The most targets to return. `None` returns every match.
    offset : `int`, optional
        How many matches to skip first.

    Returns
    -------
    targets : `Page` [`Target`]
        The matching targets.

    Raises
    ------
    InvalidArgumentError
        If a circle is incomplete, ``order_by="separation"`` has no circle,
        a choice is unknown, or ``limit`` or ``offset`` is out of range.
    """
    from astrometricslib.foundation.arguments import check_choice, check_limit
    from astrometricslib.models.page import Page
    from astrometricslib.models.target import TargetObjectType
    from astrometricslib.pipelines.shared.equipment import frame_matches

    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    if order_by is not None:
        check_choice("order_by", order_by, TARGET_ORDERS)
    if object_type is not None:
        check_choice("object_type", object_type, tuple(kind.value for kind in TargetObjectType))
    region_values = (ra_deg, dec_deg, radius_deg)
    if any(value is not None for value in region_values) and any(value is None for value in region_values):
        raise InvalidArgumentError("A region search needs ra_deg, dec_deg and radius_deg together.")
    if order_by == "separation" and radius_deg is None:
        raise InvalidArgumentError(
            "order_by='separation' needs a region search (ra_deg, dec_deg and radius_deg)."
        )
    kept: list[tuple[Any, float, str]] = []
    for target in targets:
        if text and text.lower() not in f"{target.id} {target.common_name}".lower():
            continue
        if not include_empty and not target.frames:
            continue
        if object_type is not None and target.object_type != object_type:
            continue
        lights = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
        if camera and not any(camera.lower() in (frame.camera or "").lower() for frame in lights):
            continue
        if configuration is not None and not any(
            frame_matches(frame, configuration, config) for frame in lights
        ):
            continue
        separation = 0.0
        if radius_deg is not None and ra_deg is not None and dec_deg is not None:
            try:
                separation = angular_separation_deg(ra_deg, dec_deg, target.ra_deg, target.dec_deg)
            except InvalidArgumentError:
                continue
            if separation > radius_deg:
                continue
        stamps = [frame.timestamp for frame in lights if frame.timestamp]
        kept.append((target, separation, _iso(max(stamps)) or "" if stamps else ""))
    if order_by == "name":
        kept.sort(key=lambda item: item[0].id.lower())
    elif order_by == "separation":
        kept.sort(key=lambda item: item[1])
    elif order_by == "newest":
        kept.sort(key=lambda item: item[2], reverse=True)
    return Page.of([item[0] for item in kept], offset=offset, limit=limit)


def count_frames_by_camera(targets: list[Any], config: Any) -> Any:
    """Count the light frames each camera took, overall and per target.

    Parameters
    ----------
    targets : `list` [`Target`]
        Every target.
    config : `AppConfiguration`
        The settings: the configured cameras and their profiles.

    Returns
    -------
    counts : `TargetCameraCounts`
        The frames of each camera name, and the camera index the app's
        target list uses (see `build_target_camera_index`).
    """
    from astrometricslib._internal.drivers.camera_profile_store import camera_identity
    from astrometricslib.models.catalog_queries import TargetCameraCounts
    from astrometricslib.pipelines.shared.quality.frame_statistics import list_camera_names
    from astrometricslib.pipelines.shared.target_camera_index import build_target_camera_index

    camera_names = list(config.get_available_cameras())
    return TargetCameraCounts(
        frames_by_camera=list_camera_names(targets),
        camera_index=build_target_camera_index(targets, camera_names, camera_identity),
    )


def count_nights(targets: list[Any]) -> Any:
    """Count, for each observing night, how many targets have frames from it.

    Parameters
    ----------
    targets : `list` [`Target`]
        Every target.

    Returns
    -------
    counts : `TargetNightCounts`
        Night (``YYYY-MM-DD``) to target count, in date order.
    """
    from astrometricslib.models.catalog_queries import TargetNightCounts

    return TargetNightCounts(targets_by_night=count_targets_per_night(targets))
