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
    # Step 4b (stars).
    "astrometricslib.StellarCatalog.dataset_version": (
        "astrometricslib.drivers.get_library_data_version(config) (the library's write counters)"
    ),
    "astrometricslib.StellarCatalog.find_or_create_by_position": (
        "astrometrics.stars.query(ra_deg=..., dec_deg=..., radius_deg=...) to find the star, then "
        "astrometrics.stars.create(name, ra_deg, dec_deg) for a new one"
    ),
    "astrometricslib.StellarCatalog.analyze_periodicity": (
        "astrometrics.stars.record_periodicity(star) (measure_periodicity(star) saves nothing)"
    ),
    # Step 4c: calibration is a sub-API of its own, with a `frames` child.
    "astrometricslib.ProcessingPipelines.calibration": "astrometrics.calibration",
    "astrometricslib.CalibrationCatalog.add_frames": "astrometrics.calibration.frames.add(paths)",
    "astrometricslib.CalibrationCatalog.reindex_frames": "astrometrics.calibration.frames.scan(kind)",
    "astrometricslib.CalibrationCatalog.save": (
        "astrometrics.calibration.frames.add(paths) or frames.scan(), which save the index"
    ),
    "astrometricslib.CalibrationCatalog.measure_frames": (
        "astrometrics.calibration.frames.record(kind, setting=CaptureSetting(camera=...))"
    ),
    "astrometricslib.CalibrationCatalog.camera_profile": "astrometrics.calibration.get_profile(camera)",
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
