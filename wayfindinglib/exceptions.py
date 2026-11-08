"""Purpose: The errors that belong to the wayfinding library.

Description: The error categories shared with the science library
(`NotFoundError`, `InvalidArgumentError` and the rest) come from
`astrometricslib.exceptions`. Each name here is imported from the module where
its code lives today.
"""

from wayfindinglib.data_access.delegation_policy_reader import DelegationPolicyValidationError
from wayfindinglib.models.session.observation_session import PlanRevisionConflictError

__all__ = [
    "DelegationPolicyValidationError",
    "PlanRevisionConflictError",
]
