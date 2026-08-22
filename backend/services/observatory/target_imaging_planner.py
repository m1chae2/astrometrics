"""Construction and validation of imaging sequence plans for a target."""

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class TargetImagingPlanner:
    """Service responsible for creating and validating imaging sequences."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        pass

    def create_plan(self, target_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """RPC wrapper to map items to plan_items parameter.

        Returns
        -------
        plan : `dict`
            A dictionary representing the imaging sequence card.
        """
        return self.create_sequence_plan(target_name=target_name, plan_items=items)

    def create_sequence_plan(self, target_name: str, plan_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a sequence plan object from the provided inputs.

        Parameters
        ----------
        target_name : `str`
            Name of the target (e.g., ``'M42'``).
        plan_items : `list` [`dict`]
            List of dicts, each containing ``'count'``, ``'exposure'``,
            and ``'filter'``.

        Returns
        -------
        plan : `dict`
            A dictionary representing the imaging sequence card.
        """
        logger.info(f"Creating sequence plan for {target_name} with {len(plan_items)} items")

        # Calculate total estimated duration
        total_duration = 0
        formatted_items = []

        for item in plan_items:
            count = int(item.get("count", 0))
            exposure = float(item.get("exposure", 0.0))
            filter_name = str(item.get("filter", "L"))

            duration = count * exposure
            total_duration += duration

            formatted_items.append({
                "count": count,
                "exposure": exposure,
                "filter": filter_name,
                "duration": duration,
            })

        sequence_id = str(uuid.uuid4())

        return {
            "id": sequence_id,
            "target_name": target_name,
            "items": formatted_items,
            "total_duration": total_duration,
            "created_at": "now",  # In real app, use timestamp
            "status": "planned",
        }
