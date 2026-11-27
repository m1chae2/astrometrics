"""Purpose: Check and convert the arguments that the public API methods take.

Description: The public methods on `Astrometrics` and its sub-APIs accept
friendly inputs. A target may be named by its id or passed as a `Target`
object. A time may be an ISO 8601 string, a `datetime` or an astropy `Time`.
Methods that offer several variants pick one with ``kind=`` and add optional
sections with ``include=``. Every method checks these the same way, so the
checks live here, in one place:

* `resolve_target` turns a name into the `Target` it names;
* `StackKind` and `STACK_KINDS` name the stack kind argument that several
  methods share;
* `make_frame_selection` builds the `FrameSelection` that the quality and
  stacking methods share.

The checks that both libraries share (`check_choice`, `check_include`,
`check_limit`, `reject_unused_arguments`, `TimeInput` and
`to_epoch_seconds`) live in `astrometricslib.foundation.arguments`.

Each check raises `InvalidArgumentError` or `NotFoundError`, never a
built-in exception.
"""

from typing import Literal, Protocol

from astrometricslib.foundation.arguments import TimeInput, to_epoch_seconds
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.quality.frame_selection import FrameSelection

__all__ = [
    "STACK_KINDS",
    "StackKind",
    "TargetLookup",
    "make_frame_selection",
    "resolve_target",
]

StackKind = Literal["imaging", "spectral"]
STACK_KINDS = ("imaging", "spectral")


class TargetLookup(Protocol):
    """Anything that can find a target by its id, such as `TargetCatalog`."""

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Find one target by its id.

        Parameters
        ----------
        target_id : `str`
            The target's id, matched loosely.
        refresh : `bool`, optional
            Read the stored targets first.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None` when no target has that id.
        """


def resolve_target(targets: TargetLookup | None, target: str | Target) -> Target:
    """Turn a target name into the `Target` it names.

    Parameters
    ----------
    targets : `TargetLookup` or `None`
        The catalog to look the name up in. It may be `None` when the
        caller passes `Target` objects only.
    target : `str` or `Target`
        A target id (matched loosely, as `TargetCatalog.get` does) or a
        `Target` object, which is returned as it is. A name is looked up
        after bringing it up to date with storage, so a target that another
        program (a frame sync, the app) added or changed is seen. Only that
        target's row is read (about 10 ms), and nothing is read when nothing
        has been written since the last read. The target's unsaved edits in
        memory are kept.

    Returns
    -------
    target : `Target`
        The target.

    Raises
    ------
    NotFoundError
        If the name does not match any target in the library.
    InvalidArgumentError
        If ``target`` is neither a name nor a `Target`, or a name is given
        and there is no catalog to look it up in.
    """
    if isinstance(target, Target):
        return target
    if not isinstance(target, str) or not target.strip():
        raise InvalidArgumentError("target must be a target id or a Target.")
    if targets is None:
        raise InvalidArgumentError(
            f"Pass the Target itself: there is no target catalog to look up {target!r}."
        )
    found = targets.get(target, refresh=True)
    if found is None:
        raise NotFoundError(f"No target with id {target!r} in the library.", details={"target": target})
    return found


def make_frame_selection(
    filter_name: str | None,
    first_file: str | None,
    last_file: str | None,
    since: TimeInput | None,
    until: TimeInput | None,
    include_spectra: bool,
) -> FrameSelection:
    """Build the frame choice that several methods share.

    Returns
    -------
    selection : `FrameSelection`
        The filter, file range and time window to keep.
    """
    return FrameSelection(
        filter_name=filter_name,
        first_file=first_file,
        last_file=last_file,
        since=to_epoch_seconds(since, "since"),
        until=to_epoch_seconds(until, "until"),
        include_spectra=include_spectra,
    )
