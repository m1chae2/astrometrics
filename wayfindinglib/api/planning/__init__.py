"""Purpose: The `planning` sub-API of the Wayfinder, split by object.

Description: `ObservationPlanning` (in `planning.py`) is the area object. Its
four objects each live in their own module: `sessions.py`, `packages.py`,
`sky.py` and `catalogs.py`. The owner functions behind them are under
`wayfindinglib.tasks.planning_tasks`; `PlanningContext` there holds the
settings and storage they share. Import `ObservationPlanning` from
`wayfindinglib`.
"""

from wayfindinglib.api.planning.planning import ObservationPlanning

__all__ = ["ObservationPlanning"]
