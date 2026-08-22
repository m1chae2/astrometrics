"""Target persistence operations.

Extracted SQLite and filesystem persistence operations for target
library indexes, including CRUD (create, read, update, delete)
operations, and linking target assets.

`api` throughout this module is a `TargetCatalog` instance -- it owns
the in-memory `_targets`/`_touched_target_ids` state and the `butler`
handle these functions read/write directly. Functions here call each
other directly (e.g. `create_target` calls `get_target`) rather than
routing back through `TargetCatalog`'s public methods, so this module
never calls back up into Layer 1.
"""

import os
from typing import Any


def _mark_touched(api, target_id: str) -> None:  # ruff: ignore[missing-type-function-argument]
    """Record that a target may have been fetched, created, or mutated.

    save_targets() only re-persists targets marked touched here, rather
    than every target this process happens to hold a stale in-memory copy
    of. This keeps a process that only ever looked at target A from
    clobbering a concurrent process's edits to target B when it saves.

    Parameters
    ----------
    api : `Any`
        the high-level interface holding the touched-id set.
    target_id : `str`
        The id of the target being fetched, created, or updated.
    """
    touched_ids = getattr(api, "_touched_target_ids", None)
    if touched_ids is None:
        touched_ids = set()
        api._touched_target_ids = touched_ids
    touched_ids.add(target_id)


def _find_target(targets: list[Any], target_id: str) -> Any | None:
    """Search a target list, supporting exact and fuzzy matching.

    Parameters
    ----------
    targets : `List[Any]`
        The targets to search.
    target_id : `str`
        The target identifier to look up. Tried first as an exact
        match, then with underscores normalized to spaces, then via
        case-insensitive, space/underscore-ignoring fuzzy matching.

    Returns
    -------
    target : `Optional[Any]`
        The matching target, or `None` if no target matches by any
        of the three strategies.
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
    """Return all targets currently loaded in the library.

    Reloads the full catalog from disk on every call, so callers that
    iterate every target and mutate items in place (e.g. batch reindex
    scripts) always see the latest on-disk state. Every returned target is
    marked touched, since the caller may hold and mutate a direct
    reference to any of them before the next save_targets() call.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing butler access.

    Returns
    -------
    targets : `List[Any]`
        All targets currently loaded in the library.
    """
    api._targets = api.butler.get("target_catalog", {}) or []
    for target in api._targets:
        _mark_touched(api, target.id)
    return api._targets


def get_target(api, target_id: str) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Retrieve a target by ID, supporting exact and fuzzy matching.

    Looks up target_id against the in-memory target cache first, rather
    than reloading the full catalog from disk on every call. This avoids
    orphaning mutations made to targets fetched earlier in the same
    process: e.g. fetching target A, mutating it, then fetching target B
    no longer silently discards A's in-memory edit by replacing
    api._targets wholesale. If target_id isn't found in the cache, any
    targets on disk that this process doesn't know about yet are pulled in
    (additively, without replacing already-cached targets) before retrying
    the match, so newly created records -- from this process or another --
    are still discoverable.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing target lookup.
    target_id : `str`
        The target identifier to look up. Tried first as an exact
        match, then with underscores normalized to spaces, then via
        case-insensitive, space/underscore-ignoring fuzzy matching.

    Returns
    -------
    target : `Optional[Any]`
        The matching target, or `None` if no target matches by any
        of the three strategies.
    """
    target = _find_target(api._targets, target_id)

    if target is None:
        known_ids = {existing.id for existing in api._targets}
        for fresh_target in api.butler.get("target_catalog", {}) or []:
            if fresh_target.id not in known_ids:
                api._targets.append(fresh_target)
        target = _find_target(api._targets, target_id)

    if target is not None:
        _mark_touched(api, target.id)
    return target


def create_target(api, target_id: str, ra: str | None = None, dec: str | None = None) -> Any:  # ruff: ignore[missing-type-function-argument]
    """Create a new target and scan the filesystem for matching frames.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing target lookup and
        persistence.
    target_id : `str`
        The identifier for the new target; underscores are
        normalized to spaces.
    ra : `str`, optional
        The target's right ascension, default `None`.
    dec : `str`, optional
        The target's declination, default `None`.

    Returns
    -------
    target : `Any`
        The newly created target, or the existing target if one
        already matches target_id.
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
    from astrometricslib.tasks.target_tasks.pipeline_tasks import reindex_frames

    reindex_frames(new_target)
    api._targets.append(new_target)
    _mark_touched(api, new_target.id)
    save_targets(api)
    return new_target


def update_target(api, target_id: str, updates: dict) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Update attributes on a target by ID.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing target lookup and
        persistence.
    target_id : `str`
        The identifier of the target to update.
    updates : `dict`
        Attribute name/value pairs to set on the target. Only keys
        that already exist as attributes on the target are applied;
        an "id" value is normalized to replace underscores with
        spaces.

    Returns
    -------
    target : `Optional[Any]`
        The updated target, or `None` if target_id does not match
        any target.
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
    """Remove a target from the target library index.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing target lookup and butler
        access.
    target_id : `str`
        The identifier of the target to remove.

    Returns
    -------
    was_deleted : `bool`
        `True` if a matching target was found and removed, `False`
        otherwise.
    """
    target = get_target(api, target_id)
    if target:
        targets = api.butler.get("target_catalog", {})
        filtered_targets = [t for t in targets if t.id != target.id]
        api.butler.put(filtered_targets, "target_catalog", {})
        api._targets = api.butler.get("target_catalog", {}) or []
        getattr(api, "_touched_target_ids", set()).discard(target.id)
        return True
    return False


def refresh_target(api, target_id: str, prune_missing: bool = False) -> None:  # ruff: ignore[missing-type-function-argument]
    """Rescan the filesystem directory for the target's frame records.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing target lookup, creation,
        and persistence.
    target_id : `str`
        The identifier of the target to refresh. If no target
        matches, a new one is created instead.
    prune_missing : `bool`, optional
        If `True`, clear the target's existing frame records before
        rescanning, discarding any that are no longer found on disk;
        default `False`, which keeps existing records and only adds
        newly discovered ones.
    """
    target = get_target(api, target_id)
    if not target:
        create_target(api, target_id)
        return

    if prune_missing:
        target.frames = []

    from astrometricslib.tasks.target_tasks.pipeline_tasks import reindex_frames

    reindex_frames(target)
    save_targets(api)


def save_targets(api) -> None:  # ruff: ignore[missing-type-function-argument]
    """Persist targets this process has touched back to disk.

    Only merges in targets marked touched by get_target/list_targets/
    create_target (i.e. actually fetched, created, or updated by this
    process), rather than overwriting the whole target_catalog with
    api._targets. api._targets can hold targets this process loaded once
    but never revisited, whose on-disk copies a concurrent process may
    have since changed; pushing the whole list back would silently
    clobber those concurrent edits with this process's stale copies. Only
    ever pushing what this process actually touched keeps that blast
    radius limited to records it meant to change.

    Falls back to a full-collection replace for butlers that don't
    implement merge_and_persist_records (e.g. test doubles), preserving
    the previous replace-on-save behavior for those.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing butler access and the
        in-memory target list to persist.
    """
    touched_ids = getattr(api, "_touched_target_ids", None)
    if not touched_ids:
        return

    if not hasattr(api.butler, "merge_and_persist_records"):
        api.butler.put(api._targets, "target_catalog", {})
        return

    touched_targets = [target for target in api._targets if target.id in touched_ids]
    if not touched_targets:
        return

    api.butler.merge_and_persist_records(
        "target_catalog", touched_targets, lambda existing_target, updated_target: updated_target
    )


def add_data(api, target_id: str, image_file: Any, camera: str | None = None) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Link processed image files or raw frames to a target.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing target lookup, the image
        service, and persistence.
    target_id : `str`
        The identifier of the target to link the file(s) to; created
        if it does not already exist.
    image_file : `Any`
        A single file path/dict, or a list of them. FITS files
        (.fits/.fit) are indexed as frames via the image service;
        image files (.jpg/.jpeg/.png/.tiff/.tif) are set as the
        target's processed image.
    camera : `str`, optional
        Unused by this function directly; accepted for interface
        consistency. Default `None`.

    Returns
    -------
    serialized_target : `Dict[str, Any]`
        The updated target, serialized.

    Raises
    ------
    RuntimeError
        Raised if the API's image service is not available (e.g. in
        standalone mode).
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
