"""Purpose: Private `arguments` names of `astrometricslib`.

Description: The private names of the `arguments` group: argument checks. Only
`astrometricslib` itself, and the tests of either library, may import this
module. Each name is imported from the module where its code lives today, so
moving the code later changes this file only.
"""

from astrometricslib.foundation.arguments import (
    check_choice,
    check_include,
    reject_unused_arguments,
    to_epoch_seconds,
)
from astrometricslib.pipelines.shared.api_arguments import resolve_target

__all__ = [
    "check_choice",
    "check_include",
    "reject_unused_arguments",
    "resolve_target",
    "to_epoch_seconds",
]
