"""Report what a rescan of the calibration library found.

The calibration library keeps its frame lists as nested dictionaries. The
nesting differs by kind: flats are filed by telescope, camera, filter and
gain/offset, darks by camera, gain/offset and exposure. This module turns
those lists into flat "group name to file paths" tables, compares the table
from before a rescan with the one from after, and measures each new set of
flats with the same check the stacker runs.

Everything here takes plain data and returns plain data. The public
`CalibrationCatalog` in `api/processing.py` calls it.
"""

from collections.abc import Iterable
from typing import Any

from astrometricslib.drivers.calibration_library import FlatGroup, calibration_setting_key
from astrometricslib.models.calibration_ingest import (
    CalibrationGroupChange,
    CalibrationIngestReport,
    FlatSetAssessment,
)
from astrometricslib.pipelines.stacking.pre_processing.flat_calibration import assess_flats

# Separates the levels of a group name (telescope, camera, filter, ...).
GROUP_NAME_SEPARATOR = " / "


def flatten_frame_index(index: dict[str, Any]) -> dict[str, set[str]]:
    """Turn nested frame lists into one table of file paths per group.

    Parameters
    ----------
    index : `dict`
        A library frame index: dictionaries nested to any depth, with a list
        of file paths at each end.

    Returns
    -------
    groups : `dict` [`str`, `set` [`str`]]
        Each group's name (its keys joined with `GROUP_NAME_SEPARATOR`) and
        the set of its file paths.
    """
    groups: dict[str, set[str]] = {}

    def walk(node: Any, names: tuple[str, ...]) -> None:
        """Record the lists below `node`, naming each by the keys above it."""
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, (*names, str(key)))
        elif isinstance(node, list):
            groups.setdefault(GROUP_NAME_SEPARATOR.join(names), set()).update(node)

    walk(index, ())
    return groups


def summarize_changes(
    before: dict[str, set[str]], after: dict[str, set[str]]
) -> tuple[list[CalibrationGroupChange], int, int]:
    """Compare two tables from `flatten_frame_index` and list what changed.

    Parameters
    ----------
    before : `dict` [`str`, `set` [`str`]]
        The table before the rescan.
    after : `dict` [`str`, `set` [`str`]]
        The table after the rescan.

    Returns
    -------
    changes : `list` [`CalibrationGroupChange`]
        One entry for each group that gained or lost a frame, sorted by name.
    added_count : `int`
        Frames in `after` that were not in `before`.
    removed_count : `int`
        Frames in `before` that are not in `after`.
    """
    changes = []
    added_count = 0
    removed_count = 0
    for name in sorted(before.keys() | after.keys()):
        old_paths = before.get(name, set())
        new_paths = after.get(name, set())
        added = len(new_paths - old_paths)
        removed = len(old_paths - new_paths)
        added_count += added
        removed_count += removed
        if added or removed:
            changes.append(
                CalibrationGroupChange(group=name, added=added, removed=removed, total=len(new_paths))
            )
    return changes, added_count, removed_count


def flat_group_name(group: FlatGroup) -> str:
    """Give a flat group the same name `flatten_frame_index` gives it.

    Parameters
    ----------
    group : `FlatGroup`
        A flat set from the library.

    Returns
    -------
    name : `str`
        Telescope, camera, filter and the gain/offset key, joined with
        `GROUP_NAME_SEPARATOR`.
    """
    setting_key = calibration_setting_key(group.gain, group.offset)
    return GROUP_NAME_SEPARATOR.join([group.telescope, group.camera, group.filter, setting_key])


def assess_flat_group(group: FlatGroup) -> FlatSetAssessment:
    """Measure one set of flats with the check the stacker uses.

    Parameters
    ----------
    group : `FlatGroup`
        The set to measure.

    Returns
    -------
    assessment : `FlatSetAssessment`
        The set's size, brightness, expected master-flat noise and any
        problems found.
    """
    measured = assess_flats(group.paths)
    return FlatSetAssessment(
        telescope=group.telescope,
        camera=group.camera,
        filter=group.filter,
        gain=group.gain,
        offset=group.offset,
        frame_count=measured.frame_count,
        level_fraction=measured.level_fraction,
        noise_fraction=measured.noise_fraction,
        smoothing_sigma_pixels=measured.smoothing_sigma_pixels,
        passes=not measured.issues,
        issues=list(measured.issues),
    )


def build_ingest_report(
    kind: str,
    before: dict[str, set[str]],
    after: dict[str, set[str]],
    flat_groups: Iterable[FlatGroup] = (),
) -> CalibrationIngestReport:
    """Describe what a rescan changed, and for flats how good the new sets are.

    Parameters
    ----------
    kind : `str`
        The kind of frame rescanned: "dark", "bias" or "flat".
    before : `dict` [`str`, `set` [`str`]]
        The group table before the rescan.
    after : `dict` [`str`, `set` [`str`]]
        The group table after the rescan.
    flat_groups : `Iterable` [`FlatGroup`], optional
        Every flat set in the library after the rescan. Only the sets that
        gained frames are measured. Leave empty for darks and biases.

    Returns
    -------
    report : `CalibrationIngestReport`
        The totals, the groups that changed, and the flat assessments.
    """
    changes, added_count, removed_count = summarize_changes(before, after)
    gained = {change.group for change in changes if change.added}
    assessments = [assess_flat_group(group) for group in flat_groups if flat_group_name(group) in gained]
    return CalibrationIngestReport(
        kind=kind,
        added_count=added_count,
        removed_count=removed_count,
        total_count=sum(len(paths) for paths in after.values()),
        changes=changes,
        flat_assessments=assessments,
    )
