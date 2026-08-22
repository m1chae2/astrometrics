"""Purpose: Root-function astrometrics exports for the Wayfinder api layer.

Description: The counterpart to `astrometricslib.api` -- the three
registry classes below are the entry points external callers (scripts,
backend services, other libraries) should use for Observatory Control,
Observation Planning, and Observation Execution
(`Wayfinding_Library_Architecture.md` §2.1). They delegate their work
to `wayfindinglib.tasks`, and callers should never import
`wayfindinglib.tasks` or `wayfindinglib.drivers` directly.

Every export is resolved lazily (module `__getattr__`, PEP 562) rather
than imported at module level, for the same reason
`wayfindinglib/__init__.py` does so. Python must execute this file
before any `wayfindinglib.api.*` submodule is reachable, so eagerly
importing `control_registry` here would mean that merely importing
`wayfindinglib.api.planning_registry` also pulls in the control astrometrics
and its `tasks.control_tasks` dependencies -- undermining "Planning Is
Hardware-Free" (`Wayfinding_Library_Architecture.md` §2.3.4). The
`TYPE_CHECKING`-guarded imports below never execute at runtime, so they
carry none of that cost -- they exist only so static analysis can see
each name as real for `__all__`.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wayfindinglib.api.control_registry import ObservatoryControl
    from wayfindinglib.api.execution_registry import ObservationExecution
    from wayfindinglib.api.planning_registry import ObservationPlanning

__all__ = [
    "ObservationExecution",
    "ObservationPlanning",
    "ObservatoryControl",
]


def __getattr__(name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Lazily resolve a registry export, avoiding cross-astrometrics imports.

    Returns
    -------
    resolved : `Any`
        The resolved export.

    Raises
    ------
    AttributeError
        Raised if `name` is not a lazily-resolved export.
    """
    if name == "ObservatoryControl":
        from wayfindinglib.api.control_registry import ObservatoryControl

        return ObservatoryControl
    if name == "ObservationPlanning":
        from wayfindinglib.api.planning_registry import ObservationPlanning

        return ObservationPlanning
    if name == "ObservationExecution":
        from wayfindinglib.api.execution_registry import ObservationExecution

        return ObservationExecution
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
