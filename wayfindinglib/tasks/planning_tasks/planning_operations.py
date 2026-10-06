"""Purpose: Build the imaging sequence plan the app's sequencer queue runs.

Description: `build_sequence_plan` turns a list of ``{count, exposure,
filter}`` items for one target into a `SequencePlan`, adding each item's
duration (count times exposure) and the plan's total. Nothing is stored.
"""

import logging
import time
import uuid
from typing import Any

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
