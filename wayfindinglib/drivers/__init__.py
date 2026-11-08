"""Purpose: The public door to the PHD2 client.

Description: Most of `wayfindinglib.drivers` is private: the INDI interface,
the StellarMate transfer driver, the simulators and the rest are reached
through the `Wayfinder` API. One piece is public because other programs
(the simulator, the contract recorder) must speak to PHD2, the guiding
program, with the same client the app uses:

- `PHD2Client`: connects to PHD2's event server. `events()` reads the guide
  steps. `open_session()` opens a `PHD2Session`.
- `PHD2Session`: one open connection that sends PHD2 commands (connect the
  equipment, loop, guide, stop, and questions about PHD2's state) and waits
  for events such as the end of a settle.

Both refuse a host beyond this computer when the launch is offline.

The package's other submodules (``butler``, ``indi``, ``phd2``, ...) are still
importable by the library's own code and tests. They are not part of this
public surface.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wayfindinglib.drivers.phd2.phd2_client import PHD2Client, PHD2Session

__all__ = [
    "PHD2Client",
    "PHD2Session",
]

# The public names live in this module of the package. They load on first use,
# so that importing any other `wayfindinglib.drivers` submodule (the planning
# code imports `drivers.butler`) does not pull in the PHD2 client.
_LAZY_EXPORTS = {
    "PHD2Client": "wayfindinglib.drivers.phd2.phd2_client",
    "PHD2Session": "wayfindinglib.drivers.phd2.phd2_client",
}


def __getattr__(name: str) -> Any:
    """Load a public name from its module the first time it is read.

    Parameters
    ----------
    name : `str`
        Attribute being looked up on the package.

    Returns
    -------
    value : `object`
        The named class.

    Raises
    ------
    AttributeError
        If ``name`` is not one of the public names.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
