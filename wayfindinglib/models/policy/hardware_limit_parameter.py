"""Purpose: The declaration of a hardware limit that a capability must respect.

Description: A hardware limit is a number that protects the equipment, such
as the fastest slew a mount may be asked for. It comes from the equipment
profile and has no safe default, so the resolver refuses to run without it.

`HardwareLimitParameter` is a `TunedParameter` that also names the
observatory capability whose authority the limit applies under. A subclass
may only add fields. It never changes how a value is resolved.
"""

from typing import Self

from pydantic import model_validator

from astrometricslib.parameters import ParameterRole, TunedParameter
from wayfindinglib.models.policy.delegation import ObservatoryCapability


class HardwareLimitParameter(TunedParameter):
    """A hardware-limit parameter tied to one observatory capability.

    Parameters
    ----------
    capability : `ObservatoryCapability`
        The capability whose authority the limit applies under.
    """

    capability: ObservatoryCapability

    @model_validator(mode="after")
    def _check_role(self) -> Self:
        """Check that the role is ``hardware limit``.

        Returns
        -------
        parameter : `HardwareLimitParameter`
            The same record, when the role is right.

        Raises
        ------
        ValueError
            If the role is not ``hardware limit``.
        """
        if self.role is not ParameterRole.HARDWARE_LIMIT:
            raise ValueError(f"{self.name}: a hardware limit parameter must have the role 'hardware limit'")
        return self


__all__ = ["HardwareLimitParameter"]
