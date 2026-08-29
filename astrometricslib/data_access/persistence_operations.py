"""Target database operations.

This module contains the functions that actually save, load, update, and
delete targets (like galaxies or stars) in our database. It talks directly
to the database system so the rest of the program doesn't have to.
"""

import os
from typing import Any

from astrometricslib.models.target import Target


def _mark_touched(api, target_id: str) -> None:  # ruff: ignore[missing-type-function-argument]
    """Remember that we changed a target so we know to save it later.

    This prevents us from accidentally saving over someone else's changes
    if we only modified one target but had a bunch loaded in memory.

    Parameters
    ----------
    api : `Any`
        The system that keeps track of our loaded targets.
    target_id : `str`
        The name of the target we just changed or looked at.
    """
    touched_ids = getattr(api, "_touched_target_ids", None)
    if touched_ids is None:
        touched_ids = set()
        api._touched_target_ids = touched_ids
    touched_ids.add(target_id)


def _find_target(targets: list[Any], target_id: str) -> Any | None:
    """Look for a target in our list, even if the name isn't perfectly typed.

    It tries an exact match first. If that fails, it tries replacing
    underscores with spaces, and then it tries ignoring spaces and capitals.

    Parameters
    ----------
    targets : `list`
        The list of targets to look through.
    target_id : `str`
        The name of the target we want to find.

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
    """Load and return all the targets from the database.

    This always reads fresh from the hard drive, so if another program
    added a target, we will see it.

    Parameters
    ----------
    api : `Any`
        The system that manages our database connection.

    Returns
    -------
    targets : `list`
        A list of all targets in the database.
    """
    api._targets = api.catalog_access.get("target_catalog", {}) or []
    for target in api._targets:
        _mark_touched(api, target.id)
    return api._targets


def get_target(api, target_id: str) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Find a specific target by its name.

    It looks in our already-loaded memory first so we don't wipe out
    unsaved changes. If it's not there, it checks the hard drive for
    newly added targets.

    Parameters
    ----------
    api : `Any`
        The system managing our loaded targets.
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
    """Update our saved list of images from the actual files on disk.

    This function adds any new image files it finds and updates the
    total exposure time. If `refresh_headers` is True, it will also
    re-read the FITS header data for files we already know about.
    """
    if catalog_access is None:
        from astrometricslib.data_access.catalog_access import CatalogAccess

        catalog_access = CatalogAccess()

    if prune_missing:
        target.frames = [
            f
            for f in target.frames
            if catalog_access.exists("raw_frame", {"path": f.path})
            and not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    catalog_access.get("raw_frames", {"target": target, "refresh_headers": refresh_headers})


def create_target(api, target_id: str, ra: str | None = None, dec: str | None = None) -> Any:  # ruff: ignore[missing-type-function-argument]
    """Create a new target and look for its image files on the hard drive.

    Parameters
    ----------
    api : `Any`
        The system that manages our targets.
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
        The system that manages our targets.
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
        The system that manages our targets.
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
        return True
    return False


def refresh_target(api, target_id: str, prune_missing: bool = False) -> None:  # ruff: ignore[missing-type-function-argument]
    """Check the hard drive again for new images for this target.

    Parameters
    ----------
    api : `Any`
        The system that manages our targets.
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
    """Save our changes back to the database.

    This is smart and only saves the specific targets we actually changed
    or looked at. This stops us from accidentally deleting changes that
    other parts of the program might be making at the same time.

    Parameters
    ----------
    api : `Any`
        The system that manages our targets.
    """
    touched_ids = getattr(api, "_touched_target_ids", None)
    if not touched_ids:
        return

    if not hasattr(api.catalog_access, "merge_and_persist_records"):
        api.catalog_access.put(api._targets, "target_catalog", {})
        return

    touched_targets = [target for target in api._targets if target.id in touched_ids]
    if not touched_targets:
        return

    api.catalog_access.merge_and_persist_records(
        "target_catalog", touched_targets, lambda existing_target, updated_target: updated_target
    )


def add_data(api, target_id: str, image_file: Any, camera: str | None = None) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Connect a new image file to a target.

    Parameters
    ----------
    api : `Any`
        The system that manages our targets.
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
            target.processed_image = path

    target.recalculate_total_exposure()
    save_targets(api)
    return target.serialize()
