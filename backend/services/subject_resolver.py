"""Purpose: Turn an id from the app into the library object it names.

Description: The app sends ids: a target id, a star id. The library's public
methods work on the objects themselves (`Target`, `StellarObject`), so a
service looks each id up here first and passes the object on. One place does
the lookup, so every RPC answers an unknown id the same way: a
`NotFoundError` with ``report="error"`` (the person asked for something that
is not there), which the RPC route shows as an error message.

`SubjectResolver.resolve` is keyed on the object's type, the same table the
MCP tools use (`mcp_servers/common/resolvers.py`); `target`, `star` and
`camera` are the short forms services call. A camera is named by any
spelling of its name: the cameras in the settings and in the stored
observatory configurations are searched, by name, by alias and by the names
their noise profiles list.
"""

import re
from collections.abc import Callable
from typing import Any

from astrometricslib import Camera, StellarObject, Target
from astrometricslib.exceptions import NotFoundError


def _spelling(name: str | None) -> str:
    """Reduce a camera name to lowercase letters and digits.

    Parameters
    ----------
    name : `str` or `None`
        A camera name.

    Returns
    -------
    spelling : `str`
        The name without case, spaces or punctuation.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").casefold())


def find_camera(astrometrics: Any, name: str) -> Camera | None:
    """Find the camera a name means, in any spelling.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library handle.
    name : `str`
        The camera's name, an alias, or a name its noise profile lists.

    Returns
    -------
    camera : `Camera` or `None`
        The camera from the settings, else from a stored observatory
        configuration, or `None` when none has that name.
    """
    wanted = _spelling(name)
    cameras = list(astrometrics.config.get_cameras())
    for configuration in astrometrics.observatories.configurations.query():
        cameras.extend(camera for camera in (configuration.camera, configuration.guide_camera) if camera)
    for camera in cameras:
        if wanted in {_spelling(spelling) for spelling in (camera.name, *camera.aliases)}:
            return camera
    for camera in cameras:
        profile = astrometrics.calibration.get_profile(camera)
        if not profile.is_generic_fallback and wanted in {
            _spelling(spelling)
            for spelling in (profile.camera_name, profile.record_name, *profile.name_aliases)
        }:
            return camera
    return None


class SubjectResolver:
    """Look up the library objects that RPC parameters name by id.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The backend's shared library handle.
    """

    def __init__(self, astrometrics: Any) -> None:
        """Keep the library handle and build the table of lookups."""
        self._astrometrics = astrometrics
        self._lookups: dict[type, tuple[str, Callable[[str], Any]]] = {
            Target: ("target", lambda target_id: astrometrics.targets.get(target_id)),
            StellarObject: ("star", lambda star_id: astrometrics.stars.get(star_id)),
            Camera: ("camera", lambda name: find_camera(astrometrics, name)),
        }

    def resolve(self, kind: type, object_id: Any) -> Any:
        """Give the object of a type that an id names.

        Parameters
        ----------
        kind : `type`
            The object's type, such as `Target`.
        object_id : `Any`
            The id the app sent. An object of the type itself is returned as
            it is.

        Returns
        -------
        subject : `Any`
            The object.

        Raises
        ------
        NotFoundError
            If the id names nothing. Its ``report`` is ``"error"``.

        Notes
        -----
        A type with no lookup raises `KeyError` (a programming error).
        """
        if isinstance(object_id, kind):
            return object_id
        noun, lookup = self._lookups[kind]
        found = lookup(object_id) if isinstance(object_id, str) and object_id.strip() else None
        if found is None:
            raise NotFoundError(
                f"No {noun} with id {object_id!r} in the library.", details={noun: object_id}, report="error"
            )
        return found

    def target(self, target_id: Any) -> Target:
        """Give the target an id names, up to date with storage.

        Parameters
        ----------
        target_id : `str` or `Target`
            The id (spaces, underscores and capitals may differ), or a target.

        Returns
        -------
        target : `Target`
            The target.

        Notes
        -----
        Raises `NotFoundError` if no target has that id.
        """
        return self.resolve(Target, target_id)

    def star(self, star_id: Any) -> StellarObject:
        """Give the star an id names.

        Parameters
        ----------
        star_id : `str` or `StellarObject`
            The id, or a star.

        Returns
        -------
        star : `StellarObject`
            The star.

        Notes
        -----
        Raises `NotFoundError` if no star has that id.
        """
        return self.resolve(StellarObject, star_id)

    def camera(self, name: Any) -> Camera:
        """Give the camera a name means.

        Parameters
        ----------
        name : `str` or `Camera`
            Any spelling of the camera's name, or a camera.

        Returns
        -------
        camera : `Camera`
            The camera.

        Notes
        -----
        Raises `NotFoundError` if no camera has that name.
        """
        return self.resolve(Camera, name)
