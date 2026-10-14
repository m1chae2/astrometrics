"""Purpose: Build the imaging sequence plan the app's sequencer queue runs.

Description: `build_sequence_plan` turns a list of ``{count, exposure,
filter}`` items for one target into a `SequencePlan`, adding each item's
duration (count times exposure) and the plan's total. Nothing is stored.
`exposure_requests_from_items` turns the same items into the exposure
requests of an observation package, so the sequencer's queue can be an
observing session's queue.
"""

import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError

from astrometricslib import FilterType, InvalidArgumentError
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.planning.sequence_plan import SequenceItem, SequencePlan

logger = logging.getLogger(__name__)


def build_sequence_plan(target_id: str, plan_items: list[dict[str, Any]]) -> SequencePlan:
    """Build a sequence plan for one target from plain item dictionaries.

    Parameters
    ----------
    target_id : `str`
        The target the plan is for.
    plan_items : `list` [`dict`]
        Each item's ``count``, ``exposure`` (seconds) and ``filter``
        (``"L"`` when missing).

    Returns
    -------
    plan : `SequencePlan`
        The plan, with a new id, each item's duration, the total duration
        and the creation time.
    """
    logger.info("Creating sequence plan for %s with %d items", target_id, len(plan_items))
    items = []
    for item in plan_items:
        count = int(item.get("count", 0))
        exposure = float(item.get("exposure", 0.0))
        items.append(
            SequenceItem(
                count=count, exposure=exposure, filter=str(item.get("filter", "L")), duration=count * exposure
            )
        )
    return SequencePlan(
        id=str(uuid.uuid4()),
        target_name=target_id,
        items=items,
        total_duration=sum(item.duration for item in items),
        created_at=str(time.time()),
        status="planned",
    )


def _filter_type(name: str) -> FilterType:
    """Read a filter name as a `FilterType`.

    Parameters
    ----------
    name : `str`
        A filter's value (``"Luminance"``, ``"Ha"``) or short name
        (``"L"``), in any case.

    Returns
    -------
    filter_type : `FilterType`
        The filter.

    Raises
    ------
    InvalidArgumentError
        If the name matches no filter.
    """
    wanted = name.strip().lower()
    for member_name, member in FilterType.__members__.items():
        if wanted in (member_name.lower(), member.value.lower()):
            return member
    raise InvalidArgumentError(
        f"Unknown filter {name!r}. Choose from: {', '.join(FilterType.__members__)}.",
        details={"filter": name},
    )


def exposure_requests_from_items(plan_items: list[dict[str, Any]]) -> list[ExposureRequest]:
    """Turn sequence items into the exposure requests of a package.

    Parameters
    ----------
    plan_items : `list` [`dict`]
        Each item's ``count``, ``exposure`` (seconds), ``filter`` (``"L"``
        when missing) and optional frame ``type`` (``"LIGHT"`` when
        missing).

    Returns
    -------
    requests : `list` [`ExposureRequest`]
        One request per item.

    Raises
    ------
    InvalidArgumentError
        If an item names an unknown filter or frame type, or has no
        exposure or count above zero.
    """
    requests = []
    for item in plan_items:
        frame_type = str(item.get("type") or "LIGHT").upper()
        if frame_type not in FrameType.__members__:
            raise InvalidArgumentError(f"Unknown frame type {frame_type!r}.", details={"type": frame_type})
        try:
            requests.append(
                ExposureRequest(
                    frame_type=FrameType[frame_type],
                    filter=_filter_type(str(item.get("filter") or "L")),
                    exposure_sec=float(item.get("exposure", 0.0)),
                    count=int(item.get("count", 0)),
                )
            )
        except ValidationError as error:
            raise InvalidArgumentError(
                "Each item needs an exposure and a count above zero.", details={"item": dict(item)}
            ) from error
    return requests
