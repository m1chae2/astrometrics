"""Purpose: The rename table of `astrometricslib`, and the hook that reads it.

Description: When a public method is renamed or moved, its old name stays in
this table for one release. Reading the old name on a sub-API object (and on
`Astrometrics` itself) then raises `MovedError`, whose message says
"<old name> moved to <new call>", so a saved Command Console script tells
its reader what to change. After that release the entry goes.

`RENAMES` maps the old qualified name to the new call text. The key is
``"astrometricslib.<class name>.<method name>"``, with the class whose
method moved, for example::

    "astrometricslib.TargetCatalog.select_frames":
        "astrometrics.targets.frames.query(target)",

`MovedNames` is the base class that reads the table. Every sub-API class
inherits it. Only `astrometricslib` itself, and the tests of either library,
may import this module.
"""

from typing import NoReturn

from astrometricslib.foundation.errors import MovedError

__all__ = ["RENAMES", "MovedNames"]

#: Old qualified name -> new call text. Each step of issue #206 adds the names
#: it renames or removes.
RENAMES: dict[str, str] = {
    # Step 4a: targets and their frames.
    "astrometricslib.TargetCatalog.select_frames": "astrometrics.targets.frames.query(target, ...)",
    "astrometricslib.TargetCatalog.get_frame": (
        "astrometrics.targets.frames.query(target, gain=..., exposure_s=...)[index].path"
    ),
    "astrometricslib.TargetCatalog.reindex_frames": (
        "astrometrics.targets.frames.add(target, paths) or astrometrics.targets.frames.scan(target)"
    ),
    "astrometricslib.TargetCatalog.add_frames": "astrometrics.targets.frames.add(target, paths)",
    "astrometricslib.TargetCatalog.delete_images": "astrometrics.targets.frames.delete(target, frames)",
    "astrometricslib.TargetCatalog.display_turn": "astrometrics.targets.frames.get_rotation(target, frame)",
    "astrometricslib.TargetCatalog.read_frame_header": "astrometrics.targets.frames.get_header(path)",
    "astrometricslib.TargetCatalog.sessions": "astrometrics.targets.frames.get_nights(target, night=...)",
    "astrometricslib.TargetCatalog.save": "astrometrics.targets.update(target, **changes)",
    "astrometricslib.TargetCatalog.imaged_field_centers": "astrometrics.targets.measure_field_centers()",
    "astrometricslib.QualityRecords.frame_statuses": "astrometrics.targets.frames.get_statuses(target)",
    "astrometricslib.QualityRecords.set_aside_frames": "astrometrics.targets.frames.get_set_aside(target)",
    "astrometricslib.QualityRecords.put_back_set_aside_frames": (
        "astrometrics.targets.frames.restore(target, frames)"
    ),
    "astrometricslib.QualityRecords.forget": "astrometrics.targets.frames.delete(target, frames)",
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
