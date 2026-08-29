"""Purpose: Init file for Wayfinder drivers.

Description: Defines and exposes low-level drivers including the INDI
interface, its simulator, and the StellarMate remote-transfer driver.

Resolution is lazy (module `__getattr__`, PEP 562) rather than a
top-level import: `local_database.py` and `butler.py` are also
submodules of this package, needed by Observation Planning for
recording alone, and eagerly importing INDI hardware modules here
would mean importing either one transitively imports device drivers --
directly undermining "Planning Is Hardware-Free"
(`Wayfinding_Library_Architecture.md` §2.3.4). Since Python must execute
a package's `__init__.py` before any of its submodules are reachable,
this package's own `__init__` must not import hardware modules eagerly.
The `TYPE_CHECKING`-guarded import below never executes at runtime, so
it carries none of that cost -- it exists only so static analysis can
see each name as real for `__all__`.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wayfindinglib.drivers.indi_interface import IndiInterface
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

__all__ = [
    "IndiInterface",
    "SimulatorIndiInterface",
    "StellarMateInterface",
]


def __getattr__(name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Lazily resolve driver classes, keeping sibling imports hardware-free.

    Returns
    -------
    driver_class : `type`
        The resolved driver class.

    Raises
    ------
    AttributeError
        Raised if `name` is not a lazily-resolved driver class.
    """
    if name == "IndiInterface":
        from wayfindinglib.drivers.indi_interface import IndiInterface

        return IndiInterface
    if name == "SimulatorIndiInterface":
        from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

        return SimulatorIndiInterface
    if name == "StellarMateInterface":
        from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

        return StellarMateInterface
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
