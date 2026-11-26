"""Purpose: Keep the answers that place each target's stack picture on the sky.

Description: The Planetarium can lay each target's processed stack picture
over its sky map. The library decides which files to use
(`Visualization.stack_sky_picture_files`) and where the picture lies
(`Visualization.place_stack_picture_on_sky`, a grid of sky positions read
from the stretched stack's plate solution). This module only keeps the
answers: each stack's header is read once and read again only when the
stretched stack or the picture file changes (its modification time).

A stack with no plate solution is left off the sky. That is a status, not an
error: the library logs it at the information level and nothing is shown.
"""

import os
import threading
from collections.abc import Callable
from typing import Any


class StackSkyPictures:
    """Say where each target's stack picture lies on the sky, with a cache.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library. Its ``visualization`` API names the files and places
        the picture.
    resolve_path : `Callable` [[`str`], `str`]
        Gives the spelling of a stored path that exists on this computer.
    """

    def __init__(self, astrometrics: Any, resolve_path: Callable[[str], str]) -> None:
        """Start with an empty cache."""
        self._astrometrics = astrometrics
        self._resolve_path = resolve_path
        self._cache: dict[str, tuple[tuple[float, float], dict[str, Any] | None]] = {}
        self._lock = threading.Lock()

    def entry_for(self, target: Any) -> dict[str, Any] | None:
        """Describe one target's picture on the sky, or `None` without one.

        Parameters
        ----------
        target : `Target`
            The library target.

        Returns
        -------
        entry : `dict` or `None`
            ``targetId``, ``picturePath`` (the picture file, which the app
            loads through the folders the backend serves), ``width`` and
            ``height`` (the picture's size in pixels), ``gridSize`` (points per
            side) and ``grid`` (``[ra, dec]`` pairs in degrees, row by row from
            the top-left corner). `None` when the target has no stack, no
            picture or no plate solution.
        """
        visualization = self._astrometrics.visualization
        processed, picture = visualization.stack_sky_picture_files(target)
        processed = self._resolve_path(processed) if processed else ""
        picture = self._resolve_path(picture) if picture else ""
        if not processed or not picture or not os.path.isfile(processed) or not os.path.isfile(picture):
            return None
        stamp = (os.path.getmtime(processed), os.path.getmtime(picture))
        key = f"{processed}|{picture}"
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None and cached[0] == stamp:
            entry = cached[1]
        else:
            placement = visualization.place_stack_picture_on_sky(processed, picture)
            entry = None if placement is None else placement.model_dump(by_alias=True)
            with self._lock:
                self._cache[key] = (stamp, entry)
        return None if entry is None else {**entry, "targetId": target.id}
