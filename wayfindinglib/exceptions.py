"""Purpose: The errors that belong to the wayfinding library.

Description: The error categories shared with the science library
(`NotFoundError`, `InvalidArgumentError` and the rest) come from
`astrometricslib.exceptions`. Each name here is imported from the module where
its code lives today. The simulator's two errors are defined here.
"""

from astrometricslib.exceptions import ConfigurationError
from wayfindinglib.data_access.delegation_policy_reader import DelegationPolicyValidationError
from wayfindinglib.models.session.observation_session import PlanRevisionConflictError

__all__ = [
    "DelegationPolicyValidationError",
    "GuardError",
    "PlanRevisionConflictError",
    "SimulationUnavailableError",
]


class SimulationUnavailableError(ConfigurationError):
    """The simulated observatory cannot start.

    A needed program is missing, the platform does not allow it (the packaged
    app), or a safety check refused the settings.
    """


class GuardError(ConfigurationError):
    """A simulator safety check failed; the message says which and why."""
