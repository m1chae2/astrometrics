"""Purpose: The tuned parameters and how their values are found.

Description: A tuned parameter is a number a pipeline uses that was chosen
rather than measured on the data in hand: a limit, a window, a factor or a
default. Each is declared once as a `TunedParameter`, which says what the
software does with it (its role), where its value comes from (its source),
which equipment it depends on and which register entry holds its evidence.
`collect_parameters` gathers a library's declarations into a
`ParameterRegistry`, and `resolve_parameter` finds the value to use, from the
equipment, a tuning run, the configuration or the default. A script and
`wayfindinglib` import these names from here. Each is imported from the module
where its code lives today.
"""

from astrometricslib.foundation.parameters import (
    ConfigurationValues,
    EquipmentValues,
    ParameterRegistry,
    ResolvedValue,
    TunedValues,
    collect_parameters,
)
from astrometricslib.foundation.parameters import resolve as resolve_parameter
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    WhenMissing,
)

__all__ = [
    "ConfigurationValues",
    "EquipmentKind",
    "EquipmentValues",
    "HardcodedKind",
    "ParameterRegistry",
    "ParameterRole",
    "ParameterSource",
    "ResolvedValue",
    "TunedParameter",
    "TunedValues",
    "WhenMissing",
    "collect_parameters",
    "resolve_parameter",
]
