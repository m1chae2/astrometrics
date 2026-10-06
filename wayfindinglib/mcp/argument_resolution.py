"""Purpose: Convert plain MCP client values into objects Wayfinder needs.

Description: A client of the MCP server can only send JSON, such as a
target name or a coordinate dictionary. A few Wayfinder methods take
domain objects instead (a `Target` to act on, a `SkyPosition` to slew to)
and the generic reflection engine cannot convert for them. This module
supplies those conversions and hands them to `register_astrometrics_tools`.
`planning` methods read names and times themselves, so they need no
conversion here.

Each converter raises `ValueError` with a plain message when it cannot
convert. Without that, the raw string would reach the method and fail
later with an unhelpful `'str' object has no attribute 'id'`.
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from wayfindinglib import SkyPosition


def build_argument_hooks(
    wayfinder: Any,
) -> tuple[dict[str, Callable[[Any], Any]], dict[str, Callable[[], Any]]]:
    """Build the converters and injected values for a Wayfinder instance.

    Parameters
    ----------
    wayfinder : `wayfindinglib.Wayfinder`
        The Wayfinder instance whose tools are being registered. Its
        `astrometrics` handle looks target ids up.

    Returns
    -------
    argument_resolvers : `dict` [`str`, `Callable`]
        Converters keyed by parameter name (`position`, `destination`,
        `target`).
    injected_arguments : `dict` [`str`, `Callable`]
        Factories for parameters the server supplies itself. None today.
    """

    def resolve_library_target(value: Any) -> Any:
        """Convert a target id or name into the library's `Target`.

        Parameters
        ----------
        value : `str` or `Target`
            A target id such as ``"M 52"`` (a remote folder name such as
            ``"M_52"`` also matches), or an object that is already a target.

        Returns
        -------
        target : `Target`
            The library target with that id.

        Raises
        ------
        ValueError
            If the library has no target with that id.
        """
        if not isinstance(value, str):
            return value
        # Another program (a frame sync, the app) may have changed the
        # catalog since this server loaded it, so read it fresh. That takes
        # about 0.1 s.
        target = wayfinder.astrometrics.targets.get(value, refresh=True)
        if target is None:
            raise ValueError(
                f"No target {value!r} in the library. Create it first with target_create, "
                "or check the spelling against target_list."
            )
        return target

    def resolve_sky_position(value: Any) -> Any:
        """Convert ``{"ra_deg": ..., "dec_deg": ...}`` into a `SkyPosition`.

        Parameters
        ----------
        value : `dict`, `str` or `SkyPosition`
            A coordinate dictionary. Anything else (a target id for
            `control.mount.slew`, or a ready `SkyPosition`) passes through.

        Returns
        -------
        position : `SkyPosition` or `Any`
            The position, or `value` unchanged.

        Raises
        ------
        ValueError
            If the dictionary is not a valid position.
        """
        if not isinstance(value, dict):
            return value
        try:
            return SkyPosition.model_validate(value)
        except ValidationError as error:
            raise ValueError(
                f'{value!r} is not a sky position: use {{"ra_deg": ..., "dec_deg": ...}}. {error}'
            ) from error

    argument_resolvers = {
        "position": resolve_sky_position,
        "destination": resolve_sky_position,
        "target": resolve_library_target,
    }
    injected_arguments: dict[str, Callable[[], Any]] = {}
    return argument_resolvers, injected_arguments
