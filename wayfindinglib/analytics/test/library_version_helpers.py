"""Purpose: Give a stand-in science library the real data versions.

Description: `wayfindinglib`'s caches key on the science library's opaque
data versions, which the public objects give, such as
``targets.get_data_version``. A test that uses a stand-in library but writes
real rows to a temporary library folder gives the stand-in those calls here,
so the versions follow the rows as in the app. Calls the stand-in already
has are left as they are.
"""

from types import SimpleNamespace
from typing import Any

from astrometricslib._internal.testing import data_version_calls


def _attach(holder: Any, calls: SimpleNamespace) -> None:
    """Give an object each call it lacks.

    Parameters
    ----------
    holder : `Any`
        The stand-in object (``targets``, ``quality.records`` or
        ``quality.stacks``).
    calls : `types.SimpleNamespace`
        The real calls of that object.
    """
    for name, call in vars(calls).items():
        if not hasattr(holder, name):
            setattr(holder, name, call)


def attach_library_versions(library: Any, config: Any) -> Any:
    """Give a stand-in library the real data versions of a library folder.

    Parameters
    ----------
    library : `Any`
        The stand-in, with ``quality`` (and ``quality.records``). It gains
        ``targets`` and ``stars`` when it had none.
    config : `AppConfiguration`
        The settings that name the library folder.

    Returns
    -------
    library : `Any`
        The same stand-in.
    """
    calls = data_version_calls(config)
    if not hasattr(library, "targets"):
        library.targets = SimpleNamespace()
    _attach(library.targets, calls.targets)
    _attach(
        library.quality.records if hasattr(library.quality, "records") else library.quality, calls.records
    )
    if not hasattr(library.quality, "stacks"):
        library.quality.stacks = SimpleNamespace()
    _attach(library.quality.stacks, calls.stacks)
    if not hasattr(library, "stars"):
        library.stars = SimpleNamespace()
    _attach(library.stars, calls.stars)
    return library
