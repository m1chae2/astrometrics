"""Purpose: The owner functions of the public data versions of the library.

Description: A program that keeps a result worked out from the library (for
example `wayfindinglib`'s caches) needs a cheap way to ask "has anything this
result was built from changed?". Each function here answers one such
question with an opaque text: two calls give equal texts exactly when the
data the question covers are the same, and the text says nothing else. It
holds no counts, row numbers or other storage details, so a caller cannot
depend on how the library stores its data. The texts are made from the
storage versions in `astrometricslib.foundation.storage.data_versions`,
which only the library itself reads.

* `get_kind_data_version`: everything of one kind of library data (every
  target record, every quality record, or every star), from the library's
  write counter of that kind (``targets.get_data_version()``,
  ``quality.records.get_data_version()`` and ``stars.get_data_version()``
  with no filter).
* `get_target_data_version`: one target's stored record, quality
  measurements and calibration records (``targets.get_data_version``).
* `get_target_record_versions`: each target's stored record alone
  (``targets.get_record_versions``).
* `get_camera_data_version`: the measurements and calibration records of
  one camera, or of the cameras a target used
  (``quality.records.get_camera_data_version``).
* `get_stack_data_version`: the stack-level measurements of every target,
  the calibration records and the capture settings
  (``quality.stacks.get_data_version``).
* `get_frame_group_data_version`: one frame metric of chosen frames
  (``quality.records.get_data_version`` with ``subject_ids``).

A library that cannot be read raises `StorageError`; the caller then keeps
nothing.
"""

import hashlib
from collections.abc import Collection
from typing import Any

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, StorageError
from astrometricslib.foundation.storage import data_versions
from astrometricslib.models.catalog_queries import TargetRecordVersions
from astrometricslib.models.target import Target

__all__ = [
    "get_camera_data_version",
    "get_frame_group_data_version",
    "get_kind_data_version",
    "get_stack_data_version",
    "get_target_data_version",
    "get_target_record_versions",
    "opaque_text",
]


def opaque_text(parts: Any) -> str:
    """Turn a storage version into an opaque text.

    Parameters
    ----------
    parts : `Any`
        A storage version made of plain values (numbers, text and tuples of
        them), whose ``repr`` is the same for equal values.

    Returns
    -------
    text : `str`
        32 hexadecimal characters. Equal parts give equal texts.
    """
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()[:32]


def _required(found: Any, what: str) -> Any:
    """Raise when a storage version could not be read.

    Parameters
    ----------
    found : `Any`
        The storage version, or `None` when the database could not be read.
    what : `str`
        What the version is of, for the message.

    Returns
    -------
    found : `Any`
        The version itself.

    Raises
    ------
    StorageError
        If `found` is `None`.
    """
    if found is None:
        raise StorageError(f"The data version of {what} could not be read from the library.")
    return found


def _check_target(target: Any) -> Target:
    """Refuse a target that is not a `Target`.

    Returns
    -------
    target : `Target`
        The target.

    Raises
    ------
    InvalidArgumentError
        If `target` is not a `Target`.
    """
    if not isinstance(target, Target):
        raise InvalidArgumentError("Pass the Target itself; turn an id into one with targets.get(id).")
    return target


def get_kind_data_version(config: AppConfiguration, kind: str) -> str:
    """Give a text that changes whenever any data of one kind change.

    Every write of a kind adds one to the library's write counter of that
    kind in the same transaction, so an insert, an update in place, a
    delete and a write by another program all change the text.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    kind : `str`
        ``"targets"`` (the target records, with their frames and stacks),
        ``"quality"`` (the quality measurements, capture settings and
        calibration records) or ``"stars"`` (the star catalog).

    Returns
    -------
    version : `str`
        The opaque text.

    Raises
    ------
    InvalidArgumentError
        If `kind` is not one of the three.

    Notes
    -----
    A library that cannot be read raises `StorageError`.
    """
    if kind not in data_versions.ALL_KINDS:
        raise InvalidArgumentError(f"Unknown kind of library data: {kind!r}.")
    found = _required(data_versions.get_library_data_version(config, (kind,)), f"the {kind} data")
    return opaque_text(("kind", tuple(found)))


def get_target_data_version(config: AppConfiguration, target: Target) -> str:
    """Give a text that changes whenever one target's own data change.

    It covers the target's stored record (its frame and stack records), its
    quality measurements and its calibration records. Data of other targets
    do not change it, even of the same camera.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    target : `Target`
        The target.

    Returns
    -------
    version : `str`
        The opaque text.

    Notes
    -----
    A `target` that is not a `Target` raises `InvalidArgumentError`, and a
    library that cannot be read raises `StorageError`.
    """
    target_id = _check_target(target).id
    found = _required(data_versions.get_target_data_version(config, target_id), f"target {target_id}")
    return opaque_text(("target", tuple(found)))


def get_target_record_versions(config: AppConfiguration) -> TargetRecordVersions:
    """Give a text for each target that changes when its stored record does.

    It reads every stored record once and decodes none, so a result built
    from every target's frame records can read again only the targets whose
    record changed. A quality measurement does not change a record's text.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.

    Returns
    -------
    versions : `TargetRecordVersions`
        The text of each stored target, by id. Empty when nothing is stored.

    Notes
    -----
    A library that cannot be read raises `StorageError`.
    """
    signs = _required(data_versions.get_target_document_signs(config), "the target records")
    return TargetRecordVersions(
        versions={target_id: opaque_text(("record", sign)) for target_id, sign in signs.items()}
    )


def get_camera_data_version(
    config: AppConfiguration,
    *,
    camera: str | None = None,
    target: Target | None = None,
    before_night: str | None = None,
) -> str:
    """Give a text that changes whenever the data of a camera change.

    A camera's data are the quality measurements whose capture setting names
    it, at every level, and the calibration records of its stacks. With a
    target, the cameras are those of the target's own measurements, and a
    change of that list changes the text too.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    camera : `str`, optional
        The camera name, as the capture settings store it.
    target : `Target`, optional
        Cover every camera the target's measurements were taken with,
        instead of one camera. Give exactly one of ``camera`` and ``target``.
    before_night : `str`, optional
        Count only the measurements of nights before this observing night
        (``YYYY-MM-DD``, the night of now for "finished nights only"), and
        the stack measurements that have no night. A frame measurement with
        no night is left out, because it may be from tonight. Every
        measurement counts when omitted.

    Returns
    -------
    version : `str`
        The opaque text.

    Raises
    ------
    InvalidArgumentError
        If neither or both of ``camera`` and ``target`` are given.

    Notes
    -----
    A ``target`` that is not a `Target` raises `InvalidArgumentError`, and a
    library that cannot be read raises `StorageError`.
    """
    if (camera is None) == (target is None):
        raise InvalidArgumentError("Give exactly one of camera and target.")
    if target is not None:
        target_id = _check_target(target).id
        cameras = _required(
            data_versions.get_target_camera_names(config, target_id), f"the cameras of {target_id}"
        )
    else:
        cameras = (str(camera),)
    found = _required(
        data_versions.get_camera_data_versions(config, cameras, finished_before_night=before_night),
        "the camera data",
    )
    return opaque_text(("cameras", tuple(cameras), tuple(sorted(found.items()))))


def get_stack_data_version(config: AppConfiguration) -> str:
    """Give a text that changes whenever the library's stack-level data do.

    It covers the stack-level quality measurements of every target, the
    calibration records and the capture settings. A new frame of any target
    writes frame rows only, so it leaves the text as it is; a stack run
    changes it.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.

    Returns
    -------
    version : `str`
        The opaque text.

    Notes
    -----
    A library that cannot be read raises `StorageError`.
    """
    found = _required(data_versions.get_stack_quality_data_version(config), "the stack data")
    return opaque_text(("stacks", tuple(found)))


def get_frame_group_data_version(config: AppConfiguration, metric: str, subject_ids: Collection[str]) -> str:
    """Give a text that changes whenever one metric's rows of some frames do.

    Only the frame-level rows of the metric whose subject is one of the
    given frames count. A row of another frame, metric or level does not.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    metric : `str`
        The metric name.
    subject_ids : `~collections.abc.Collection` [`str`]
        The frames' subject ids (see `Target.frame_subject_id`).

    Returns
    -------
    version : `str`
        The opaque text.

    Notes
    -----
    A library that cannot be read raises `StorageError`.
    """
    found = _required(
        data_versions.get_frame_metric_signs(config, metric, (frozenset(subject_ids),)), "the frame rows"
    )
    return opaque_text(("frames", metric, tuple(found[0])))
