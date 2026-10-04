"""Target catalog operations.

This module contains the functions that actually save, load, update, and
delete targets (like galaxies or stars) in the catalog. It goes through
`CatalogAccess` to reach the database, the same way every other part of
the program does, so nothing here opens a database connection itself.
"""

import hashlib
import os
from typing import Any

from astrometricslib.models.target import Target


def _mark_touched(api, target_id: str) -> None:  # ruff: ignore[missing-type-function-argument]
    """Remember that a target was changed so it can be saved later.

    This prevents accidentally saving over someone else's changes
    if only one target was modified but a bunch were loaded in memory.

    Parameters
    ----------
    api : `Any`
        The system that keeps track of loaded targets.
    target_id : `str`
        The name of the target just changed or looked at.
    """
    touched_ids = getattr(api, "_touched_target_ids", None)
    if touched_ids is None:
        touched_ids = set()
        api._touched_target_ids = touched_ids
    touched_ids.add(target_id)


def _fingerprint(target: Target) -> str:
    """Summarize everything a target holds as a short fixed-size code.

    Two targets with the same contents give the same code, and any edit to
    the target, its frames or its stacks changes it.

    Parameters
    ----------
    target : `Target`
        The target to summarize.

    Returns
    -------
    fingerprint : `str`
        A short code for the target's current contents.
    """
    return hashlib.blake2b(target.model_dump_json().encode("utf-8"), digest_size=16).hexdigest()


def _saved_fingerprints(api) -> dict[str, str]:  # ruff: ignore[missing-type-function-argument]
    """Get the codes of the target contents as last read or saved.

    Parameters
    ----------
    api : `Any`
        The system that keeps track of loaded targets.

    Returns
    -------
    fingerprints : `dict` [`str`, `str`]
        The code of each target's contents when this process last read it
        from storage or wrote it there, by target id. The dict is created on
        first use.
    """
    fingerprints = getattr(api, "_saved_fingerprints", None)
    if fingerprints is None:
        fingerprints = {}
        api._saved_fingerprints = fingerprints
    return fingerprints


def remember_stored_state(api, targets: list[Any]) -> None:  # ruff: ignore[missing-type-function-argument]
    """Record that these targets match what is in storage right now.

    Later, `save_targets` writes only the targets whose contents have changed
    since this point, and `list_targets` refreshes only the ones that have
    not.

    Parameters
    ----------
    api : `Any`
        The system that keeps track of loaded targets.
    targets : `list`
        Targets just read from, or written to, storage.
    """
    fingerprints = _saved_fingerprints(api)
    for target in targets:
        fingerprints[target.id] = _fingerprint(target)


def _has_unsaved_changes(api, target: Target) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Tell whether a target differs from what this process last stored.

    Parameters
    ----------
    api : `Any`
        The system that keeps track of loaded targets.
    target : `Target`
        The in-memory target to check.

    Returns
    -------
    changed : `bool`
        `True` if the target was edited since it was read or saved, or if
        this process has no record of its stored state.
    """
    return _fingerprint(target) != _saved_fingerprints(api).get(target.id)


def _find_target(targets: list[Any], target_id: str) -> Any | None:
    """Look for a target in the list, even if the name isn't perfectly typed.

    It tries an exact match first. If that fails, it tries replacing
    underscores with spaces, and then it tries ignoring spaces and capitals.

    Parameters
    ----------
    targets : `list`
        The list of targets to look through.
    target_id : `str`
        The name of the target to find.

    Returns
    -------
    target : `Any` or `None`
        The found target, or None if it wasn't in the list.
    """
    # 1. Exact match
    for target in targets:
        if target.id == target_id:
            return target

    # 2. Normalized space variant
    normalized_id = target_id.replace("_", " ")
    for target in targets:
        if target.id == normalized_id:
            return target

    # 3. Fuzzy case-insensitive space-ignoring match
    def fuzzy_normalize(name: str) -> str:
        """Normalize a target name for fuzzy comparison.

        Returns
        -------
        normalized_name : `str`
            The lowercased name with spaces, underscores (and
            hyphens, where applicable) removed.
        """
        return name.lower().replace(" ", "").replace("_", "")

    normalized_target_id = fuzzy_normalize(target_id)
    for target in targets:
        if fuzzy_normalize(target.id) == normalized_target_id:
            return target

    return None


def list_targets(api) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Return all the targets, seeing what other programs have saved.

    It reads the stored targets fresh, so a target another program added or
    changed shows up. A target this process already holds keeps its object:
    code that fetched it earlier (a stack that takes minutes to run) is still
    editing the object the catalog holds, so its edits are not lost. If the
    held target has no unsaved edits, the object is refreshed in place with
    what is stored. If it has, the held object is kept as it is.

    Every listed target counts as touched, so scripts that edit the targets
    they list can save them. A save writes only those that changed.

    Parameters
    ----------
    api : `Any`
        The system that manages the database connection.

    Returns
    -------
    targets : `list`
        A list of all targets in the database.
    """
    stored_targets = api.catalog_access.get("target_catalog", {}) or []
    held = {target.id: target for target in api._targets}
    fingerprints = _saved_fingerprints(api)
    merged = []
    for stored in stored_targets:
        current = held.get(stored.id)
        if current is None:
            merged.append(stored)
            fingerprints[stored.id] = _fingerprint(stored)
            continue
        if not _has_unsaved_changes(api, current):
            # Same object, new contents: assigning fields one by one would
            # check each value again, so the stored values are moved over.
            current.__dict__.update(stored.__dict__)
            current.__pydantic_fields_set__ = set(stored.__pydantic_fields_set__)
            fingerprints[current.id] = _fingerprint(current)
        merged.append(current)
    api._targets = merged
    for target in merged:
        _mark_touched(api, target.id)
    return merged


def get_target(api, target_id: str) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Find a specific target by its name.

    It looks in already-loaded memory first so unsaved changes aren't wiped
    out. If it's not there, it checks the hard drive for
    newly added targets.

    Parameters
    ----------
    api : `Any`
        The system managing the loaded targets.
    target_id : `str`
        The name of the target to find.

    Returns
    -------
    target : `Any` or `None`
        The target object if found, otherwise None.
    """
    target = _find_target(api._targets, target_id)

    if target is None:
        known_ids = {existing.id for existing in api._targets}
        for fresh_target in api.catalog_access.get("target_catalog", {}) or []:
            if fresh_target.id not in known_ids:
                api._targets.append(fresh_target)
                remember_stored_state(api, [fresh_target])
        target = _find_target(api._targets, target_id)

    if target is not None:
        _mark_touched(api, target.id)
    return target


def reindex_frames(
    target: Target,
    prune_missing: bool = False,
    catalog_access=None,  # ruff: ignore[missing-type-function-argument]
    refresh_headers: bool = False,
) -> None:
    """Update the saved list of images from the actual files on disk.

    This function adds any new image files it finds and updates the
    total exposure time. If `refresh_headers` is True, it will also
    re-read the FITS header data for files already known.
    """
    from astrometricslib.pipelines.shared import frame_scanning

    if catalog_access is None:
        from astrometricslib.drivers.catalog_access import CatalogAccess

        catalog_access = CatalogAccess()

    if prune_missing:
        target.frames = [
            f
            for f in target.frames
            if catalog_access.exists("raw_frame", {"path": f.path})
            and not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    frame_scanning.scan_target_directory(
        target, catalog_access.config.get_frames_path(), refresh_headers=refresh_headers
    )


def create_target(api, target_id: str, ra: str | None = None, dec: str | None = None) -> Any:  # ruff: ignore[missing-type-function-argument]
    """Create a new target and look for its image files on the hard drive.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    target_id : `str`
        The name for the new target.
    ra : `str`, optional
        The right ascension (horizontal coordinate) in the sky.
    dec : `str`, optional
        The declination (vertical coordinate) in the sky.

    Returns
    -------
    target : `Any`
        The newly created target (or the existing one if it already was there).
    """
    from astrometricslib.models.target import Target

    normalized_id = target_id.replace("_", " ")
    existing = get_target(api, normalized_id)
    if existing:
        return existing

    new_target = Target(id=normalized_id)
    if ra:
        new_target.ra = ra
    if dec:
        new_target.dec = dec

    # Scan filesystem for frames matching the new target ID
    reindex_frames(new_target)
    api._targets.append(new_target)
    _mark_touched(api, new_target.id)
    save_targets(api)
    return new_target


def update_target(api, target_id: str, updates: dict) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Change specific information about a target.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    target_id : `str`
        The name of the target to update.
    updates : `dict`
        A dictionary where the keys are what to change (like "ra")
        and the values are the new information.

    Returns
    -------
    target : `Any` or `None`
        The updated target, or None if the target wasn't found.
    """
    target = get_target(api, target_id)
    if not target:
        return None

    for key, value in updates.items():
        if hasattr(target, key):
            if key == "id" and isinstance(value, str):
                value = value.replace("_", " ")
            setattr(target, key, value)

    save_targets(api)
    return target


def delete_target(api, target_id: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Remove a target completely from the database.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    target_id : `str`
        The name of the target to remove.

    Returns
    -------
    was_deleted : `bool`
        True if it was successfully deleted, False if it wasn't found.
    """
    target = get_target(api, target_id)
    if target:
        targets = api.catalog_access.get("target_catalog", {})
        filtered_targets = [t for t in targets if t.id != target.id]
        api.catalog_access.put(filtered_targets, "target_catalog", {})
        api._targets = api.catalog_access.get("target_catalog", {}) or []
        getattr(api, "_touched_target_ids", set()).discard(target.id)
        _saved_fingerprints(api).pop(target.id, None)
        remember_stored_state(api, api._targets)
        return True
    return False


def refresh_target(api, target_id: str, prune_missing: bool = False) -> None:  # ruff: ignore[missing-type-function-argument]
    """Check the hard drive again for new images for this target.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    target_id : `str`
        The name of the target to check.
    prune_missing : `bool`, optional
        If True, it will also remove records for files that have been deleted
        from the hard drive. Defaults to False.
    """
    target = get_target(api, target_id)
    if not target:
        create_target(api, target_id)
        return

    if prune_missing:
        target.frames = []

    reindex_frames(target)
    save_targets(api)


def save_targets(api) -> None:  # ruff: ignore[missing-type-function-argument]
    """Save changes back to the database.

    This writes only the targets that were touched and that really changed
    since this process read or last saved them. A target that was only
    looked at is left alone, so a stale copy of it cannot overwrite what
    another program saved in the meantime.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    """
    touched_ids = getattr(api, "_touched_target_ids", None)
    if not touched_ids:
        return

    if not hasattr(api.catalog_access, "merge_and_record"):
        api.catalog_access.put(api._targets, "target_catalog", {})
        remember_stored_state(api, api._targets)
        return

    changed_targets = [
        target for target in api._targets if target.id in touched_ids and _has_unsaved_changes(api, target)
    ]
    if not changed_targets:
        return

    api.catalog_access.merge_and_record(
        "target_catalog", changed_targets, lambda existing_target, updated_target: updated_target
    )
    remember_stored_state(api, changed_targets)


def read_saved_target(api, target_id: str) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Read one target's saved record straight from storage.

    The in-memory catalog is not used, so the answer is what another program
    would see.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    target_id : `str`
        The exact id of the target.

    Returns
    -------
    target : `Any` or `None`
        The stored target, or `None` if nothing is stored under that id.
    """
    stored = api.catalog_access.get_by_ids("target_catalog", [target_id])
    return stored[0] if stored else None


def add_data(api, target_id: str, image_file: Any, camera: str | None = None) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Connect a new image file to a target.

    Parameters
    ----------
    api : `Any`
        The system that manages the targets.
    target_id : `str`
        The name of the target the image belongs to.
    image_file : `Any`
        The file path (or a list of paths) to the new images.
    camera : `str`, optional
        The name of the camera (not currently used here).

    Returns
    -------
    serialized_target : `dict`
        A dictionary representation of the updated target.

    Raises
    ------
    RuntimeError
        If the image processing system is turned off.
    """
    target = get_target(api, target_id)
    if not target:
        target = create_target(api, target_id)

    if isinstance(image_file, str):
        files = [image_file]
    elif isinstance(image_file, list):
        files = image_file
    else:
        files = []

    if not api._image_service:
        raise RuntimeError("Image service is not available in standalone mode.")

    for f in files:
        path = f.get("path") if isinstance(f, dict) else f
        if not isinstance(path, str):
            continue

        ext = os.path.splitext(path)[1].lower()
        if ext in [".fits", ".fit"]:
            api._image_service.add_frame_to_target(target, path)
        elif ext in [".jpg", ".jpeg", ".png", ".tiff", ".tif"]:
            if not os.path.isabs(path):
                resolved = api._resolve_relative_image_path(path)
                if resolved:
                    path = resolved
            target.stacking.processed_image = path

    target.recalculate_total_exposure()
    save_targets(api)
    return target.serialize()
