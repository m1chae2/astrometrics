"""Purpose: The rename table of `wayfindinglib`, and the hook that reads it.

Description: The same mechanism as astrometricslib's rename table. When a
public method is renamed or moved, its old name stays in `RENAMES` for one
release, and reading it on a sub-API object (or on `Wayfinder`) raises
`MovedError` with "<old name> moved to <new call>". The key is
``"wayfindinglib.<class name>.<method name>"``, for example::

    "wayfindinglib.ObservationPlanning.get_plan":
        "wayfinder.planning.sessions.get(night)",

`MovedNames` is the base class that reads the table. Every sub-API class
inherits it. Only `wayfindinglib` itself, and the tests of either library,
may import this module.
"""

from typing import NoReturn

from astrometricslib.exceptions import MovedError

__all__ = ["RENAMES", "MovedNames"]

#: Old qualified name -> new call text. Each step of issue #206 adds the names
#: it renames or removes.
RENAMES: dict[str, str] = {
    "wayfindinglib.ObservationExecution.build_divergence_record": (
        "wayfindinglib's internals (record_divergence); it is no longer public"
    ),
}


class MovedNames:
    """A base class whose old method names raise `MovedError`.

    Python calls `__getattr__` only when the normal lookup finds nothing, so
    the table costs nothing on a name that exists.
    """

    def __getattr__(self, name: str) -> NoReturn:
        """Refuse a name the object does not have, saying where it moved.

        Parameters
        ----------
        name : `str`
            The name that was read.

        Raises
        ------
        AttributeError
            Always. It is a `MovedError` (which is an `AttributeError`) when
            the rename table lists the name for this class or a class it
            inherits from, and a plain `AttributeError` otherwise.
        """
        moved = MovedError.lookup(RENAMES, type(self), name)
        if moved is not None:
            raise moved
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}", name=name, obj=self)
