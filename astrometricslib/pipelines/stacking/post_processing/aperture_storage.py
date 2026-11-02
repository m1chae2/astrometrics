"""Keeping and reloading the empty apertures beside a target's stacks.

`load_apertures`, `load_apertures_with_reason` and `reset_apertures` read and
forget the small file beside the target's stacks (``quality_apertures.json``)
that keeps one set for each camera, optic and reference exposure length (see
`setup_key`). `store_apertures_once` writes a set under a lock, and
`find_library_set_key` and `record_library_set_key` remember which set the
library stack uses.

The set itself is described in `empty_apertures.py` and chosen in
`aperture_placement.py`.
"""

import hashlib
import json
import logging
import os
import tempfile

from astrometricslib.foundation.paths import locks_directory
from astrometricslib.foundation.storage.process_locks import file_lock
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    APERTURE_FILE_VERSION,
    ApertureSet,
    equipment_of_key,
    is_legacy_setup_key,
)

logger = logging.getLogger(__name__)


# The file beside a target's stacks that keeps its sky apertures.
APERTURE_FILE_NAME = "quality_apertures.json"

# The reason a subset stack gives when the stored apertures are missing or of
# an older version: a subset never chooses apertures, so that every subset of a
# target is read in the apertures its library stack chose.
OLD_APERTURES_REASON = (
    f"the target's apertures predate version {APERTURE_FILE_VERSION}; "
    "stack the target once so its library stack chooses them"
)


def _read_aperture_file(path: str) -> dict[str, dict]:
    """Read the sets stored in a target's aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key. Empty when the file is
        missing, cannot be read, or has the layout of the first development
        version (those sets lack the reference brightnesses and are not
        used).
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != APERTURE_FILE_VERSION:
            logger.warning("The stored noise apertures at %s have an old layout and will be replaced.", path)
            return {}
        return dict(data["sets"])
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return {}


def load_apertures(path: str, setup_key: str | None = None) -> ApertureSet | None:
    """Read a target's stored apertures.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The camera, optic and reference exposure wanted. Left out, the set
        the library stack recorded (see `record_library_set_key`), or in an
        older file the deepest stored set (see `_deepest_key`). A set stored
        under a key with no exposure (`is_legacy_setup_key`) is never returned.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The stored set, or `None` when the file is missing or cannot be read
        (a warning says so in the second case), or holds no set for the key.
    """
    sets = _read_aperture_file(path)
    if setup_key is None:
        data = sets.get(_default_key(sets, _read_library_keys(path)) or "")
    elif is_legacy_setup_key(setup_key):
        data = None
    else:
        data = sets.get(setup_key)
    if data is None:
        return None
    try:
        return ApertureSet.from_json(data)
    except (ValueError, KeyError, TypeError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return None


def _deepest_key(sets: dict[str, dict], equipment: str | None = None) -> str | None:
    """Pick the deepest stored set, the fallback for the library stack's set.

    A subset stack or an odd–even difference job has to use the library stack's
    set, whatever exposure length its own reference frame has. The record
    written by `record_library_set_key` names that set. This depth rule is
    used only when there is no record (a file written before it existed):
    the library stack's set is then taken to be the one chosen on the deepest
    stack. It can be wrong, because a stale set from an old stack can be
    deeper than the current stack's.

    Parameters
    ----------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    equipment : `str`, optional
        Only keys of this camera and optic are considered. Left out, all.

    Returns
    -------
    setup_key : `str` or `None`
        The key of the set with the largest effective frame count (an unknown
        count counts as 0; the first stored wins a tie), or `None` when there
        is no usable key. Keys without an exposure are skipped.
    """
    best_key = None
    best_depth = -1.0
    for key, data in sets.items():
        if is_legacy_setup_key(key) or (equipment is not None and equipment_of_key(key) != equipment):
            continue
        try:
            depth = float(data.get("defining_effective_frame_count") or 0.0)
        except AttributeError, TypeError, ValueError:
            depth = 0.0
        if depth > best_depth:
            best_key, best_depth = key, depth
    return best_key


def _default_key(sets: dict[str, dict], library_keys: dict[str, str]) -> str | None:
    """Pick the set to use when the caller names no camera or exposure.

    Parameters
    ----------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    library_keys : `dict` [`str`, `str`]
        The recorded library set key of each camera and optic.

    Returns
    -------
    setup_key : `str` or `None`
        The deepest of the recorded library sets that are still stored, or,
        when none is recorded, the deepest stored set (`_deepest_key`).
    """
    recorded = {key: sets[key] for key in library_keys.values() if key in sets}
    return _deepest_key(recorded or sets)


def find_library_set_key(path: str, setup_key: str) -> str | None:
    """Find the key of the library stack's set for a stack that may not choose.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`
        The key the stack itself would have (see `setup_key`).

    Returns
    -------
    library_key : `str` or `None`
        The set the library stack recorded for the same camera and optic
        (`record_library_set_key`), whatever its exposure length, when it is
        still stored. Otherwise (a file written before the record existed)
        the deepest stored set of that camera and optic, or `None` when there
        is none. Depth alone can pick a stale set: an old stack of one
        exposure length can be deeper, by effective frame count, than the
        current stack of folded exposure groups, whose unequal weights lower
        its count.
    """
    sets = _read_aperture_file(path)
    equipment = equipment_of_key(setup_key)
    recorded = _read_library_keys(path).get(equipment)
    if recorded is not None and recorded in sets and not is_legacy_setup_key(recorded):
        return recorded
    return _deepest_key(sets, equipment)


def _read_library_keys(path: str) -> dict[str, str]:
    """Read which set each camera and optic's library stack uses.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    library_keys : `dict` [`str`, `str`]
        The setup key of the library stack's set, by camera and optic (see
        `equipment_of_key`). Empty when the file is missing, unreadable, of
        another version, or written before the record existed.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != APERTURE_FILE_VERSION:
            return {}
        return {
            str(equipment): str(key) for equipment, key in dict(data.get("library_set_keys") or {}).items()
        }
    except OSError, ValueError, AttributeError, TypeError:
        return {}


def record_library_set_key(path: str, setup_key: str) -> None:
    """Record that a library stack uses a set, so other stacks find that one.

    A library stack (one that may choose apertures) calls this after it has
    measured its noise in a set, whether it loaded the set or made it. The key
    is kept under the set's camera and optic and replaces the earlier record.
    Stale sets are not deleted. The file keeps the same version: an older
    reader ignores the extra mapping, and a file without it still loads.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`
        The key of the set the library stack used. Nothing is recorded when
        the file holds no set under that key.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        sets = _read_aperture_file(path)
        if setup_key not in sets or is_legacy_setup_key(setup_key):
            return
        library_keys = _read_library_keys(path)
        equipment = equipment_of_key(setup_key)
        if library_keys.get(equipment) == setup_key:
            return
        library_keys[equipment] = setup_key
        _write_aperture_file(path, sets, library_keys)


def load_apertures_with_reason(path: str, setup_key: str | None = None) -> tuple[ApertureSet | None, str]:
    """Read a target's stored apertures, and say why not when there are none.

    A subset stack uses this: it never chooses apertures, so it must tell a
    missing file from a file of an older version.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The camera, optic and reference exposure wanted. Left out, the
        deepest stored set (see `load_apertures`).

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The stored set, or `None`.
    reason : `str`
        Why there is no set. Empty when there is one. A missing file, a file
        of an older version (`OLD_APERTURES_REASON`), a file with no set for
        this camera and optic, and an unreadable file each have their own
        sentence.
    """
    if not os.path.isfile(path):
        return None, (
            "the target has no stored noise apertures; stack the target once so its library stack "
            "chooses them"
        )
    if _stored_file_version(path) != APERTURE_FILE_VERSION:
        return None, OLD_APERTURES_REASON
    aperture_set = load_apertures(path, setup_key)
    if aperture_set is None:
        return None, (
            "the target's stored noise apertures hold no usable set for this camera, optic and "
            f"reference exposure ({setup_key}); stack the target once so its library stack chooses one"
        )
    return aperture_set, ""


def _stored_file_version(path: str) -> int | None:
    """Read the layout version written in an aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    version : `int` or `None`
        The version, or `None` when the file cannot be read or has none.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            version = json.load(handle).get("version")
        return int(version) if version is not None else None
    except OSError, ValueError, AttributeError, TypeError:
        return None


def _aperture_lock_path(path: str) -> str:
    """Name the lock file that guards one aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    lock_path : `str`
        A file in the locks folder, named after the aperture file's path.
    """
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return os.path.join(locks_directory(), f"quality_apertures_{digest}.lock")


def _write_aperture_file(
    path: str, sets: dict[str, dict], library_keys: dict[str, str] | None = None
) -> None:
    """Write the aperture file whole, in one step.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    library_keys : `dict` [`str`, `str`], optional
        The library stack's set key by camera and optic. Left out, the record
        already in the file is kept. Entries whose set is not in ``sets`` are
        dropped.
    """
    if library_keys is None:
        library_keys = _read_library_keys(path)
    library_keys = {equipment: key for equipment, key in library_keys.items() if key in sets}
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".quality_apertures_", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": APERTURE_FILE_VERSION, "sets": sets, "library_set_keys": library_keys}, handle
            )
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def store_apertures_once(
    path: str, aperture_set: ApertureSet, replace_identity: str | None = None
) -> ApertureSet:
    """Save the set unless another run saved one for the same equipment first.

    The file is read, changed and replaced while holding a lock, so two
    stacks of one target that finish together cannot overwrite each other's
    apertures; the loser uses the winner's.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    aperture_set : `ApertureSet`
        The set to save.
    replace_identity : `str`, optional
        The identity of the stored set this one replaces (a deeper stack chose
        a new set). The stored set is replaced only if it still has this
        identity; if another run replaced it first, that run's set is kept.

    Returns
    -------
    stored : `ApertureSet`
        The set now in the file for this equipment: this one, or the other
        run's.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        sets = _read_aperture_file(path)
        existing = sets.get(aperture_set.setup_key)
        if existing is not None:
            try:
                stored = ApertureSet.from_json(existing)
                if replace_identity is None or stored.identity != replace_identity:
                    return stored
            except ValueError, KeyError, TypeError:
                logger.warning("A stored set of noise apertures at %s was unreadable and is replaced.", path)
        # A set stored under a key with no exposure (an older file) is never
        # used again, so the new set for the same equipment takes its place.
        equipment = equipment_of_key(aperture_set.setup_key)
        for key in [key for key in sets if is_legacy_setup_key(key) and equipment_of_key(key) == equipment]:
            del sets[key]
        sets[aperture_set.setup_key] = aperture_set.to_json()
        _write_aperture_file(path, sets)
    return aperture_set


def reset_apertures(path: str, setup_key: str | None = None) -> bool:
    """Forget a target's stored apertures, so the next stack chooses new ones.

    Use this when the stored stars or patches are no longer good (a nebula
    grew, a star was misjudged) or the equipment's key changed by accident.
    Noise values measured before the reset and after it are not comparable.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The one camera, optic and reference exposure to forget. Left out,
        every set is forgotten
        and the file is deleted.

    Returns
    -------
    removed : `bool`
        `True` when something was removed.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        if setup_key is None:
            if not os.path.isfile(path):
                return False
            os.unlink(path)
            return True
        sets = _read_aperture_file(path)
        if setup_key not in sets:
            return False
        del sets[setup_key]
        if sets:
            _write_aperture_file(path, sets)
        else:
            os.unlink(path)
        return True


__all__ = [
    "APERTURE_FILE_NAME",
    "find_library_set_key",
    "load_apertures",
    "load_apertures_with_reason",
    "record_library_set_key",
    "reset_apertures",
    "store_apertures_once",
]
