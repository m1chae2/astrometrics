"""Purpose: Name a frame and tell whether it is a spectroscopy frame.

Description: Two small rules about a frame record that both the models and the
pipelines need. `frame_subject_id` gives the id that the frame's quality rows
carry. `frame_is_spectral` says whether a frame's filter is the spectroscopy
grating. They live here, below the models and the pipelines, so a model can
use them without importing a pipeline.
"""

import os
from typing import Any

from astrometricslib.foundation.enums import FilterType

__all__ = ["frame_is_spectral", "frame_subject_id"]


def frame_subject_id(path: str, target_id: str) -> str:
    """Name a frame by its target and its place in the target's lights folder.

    The library keeps frames at ``<frames root>/lights/<target>/<telescope>/
    <camera>/<file>``. The id keeps what follows the target folder, so it does
    not change when the frames root moves to another mount.

    Parameters
    ----------
    path : `str`
        The frame's path.
    target_id : `str`
        The target's id.

    Returns
    -------
    subject_id : `str`
        ``"<target_id>/<path below the target's lights folder>"``. A path with
        no ``lights`` folder above a target folder gives the target id and the
        file name only, never the full path.
    """
    parts = os.path.normpath(path).split(os.sep)
    lights_positions = [
        index for index, part in enumerate(parts) if part.lower() == "lights" and index + 2 < len(parts)
    ]
    relative_parts = parts[lights_positions[-1] + 2 :] if lights_positions else parts[-1:]
    return "/".join([target_id, *relative_parts])


def frame_is_spectral(frame: Any) -> bool:
    """Decide whether a frame is a spectroscopy frame.

    A frame's filter can show up as an already-normalized `FilterType`
    member, a bare string that skipped normalization, or the catalog
    value itself ("Star Analyzer 200") rather than the "SPEC" alias --
    checking all three keeps spectral frames recognized the same way
    everywhere they're classified, instead of each caller growing its
    own slightly different test.

    Parameters
    ----------
    frame : `Any`
        The frame record (or anything with a `.filter` attribute or
        `"filter"` key) to check.

    Returns
    -------
    is_spectral : `bool`
        True if the frame is a spectroscopy frame.
    """
    if isinstance(frame, dict):
        frame_filter = frame.get("filter")
    else:
        frame_filter = getattr(frame, "filter", None)
    return (
        frame_filter == FilterType.SPEC
        or getattr(frame_filter, "name", None) == "SPEC"
        or str(frame_filter).upper() in ("SPEC", "STAR ANALYZER 200", "SPECTROSCOPY")
    )
