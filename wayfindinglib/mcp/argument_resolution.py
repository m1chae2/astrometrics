"""Purpose: Convert plain MCP client values into objects Wayfinder needs.

Description: A client of the MCP server can only send JSON, such as a
target name or an ISO time string. Several Wayfinder methods take domain
objects instead (a `Target`, a list of `Target` or `StellarObject`, an
`Astrometrics` handle, an astropy `Time`) and have no type hints, so the
generic reflection engine cannot convert for them. This module supplies
the missing conversions and hands them to `register_astrometrics_tools`.

Each converter raises `ValueError` with a plain message when it cannot
convert. Without that, the raw string would reach the method and fail
later with an unhelpful `'str' object has no attribute 'id'`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from astropy.time import Time
from pydantic import ValidationError

from astrometricslib import StellarObject, Target
from wayfindinglib import SkyPosition


def build_argument_hooks(
    wayfinder: Any,
) -> tuple[dict[str, Callable[[Any], Any]], dict[str, Callable[[], Any]]]:
    """Build the converters and injected values for a Wayfinder instance.

    Parameters
    ----------
    wayfinder : `wayfindinglib.Wayfinder`
        The Wayfinder instance whose tools are being registered. Its
        `config` locates the library, and its `planning` interface
        resolves names.

    Returns
    -------
    argument_resolvers : `dict` [`str`, `Callable`]
        Converters keyed by parameter name (`position`, `destination`,
        `target`, `objects`, `time_input`).
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

    def resolve_sky_objects(value: Any) -> Any:
        """Convert a list of names or coordinates into sky objects.

        Parameters
        ----------
        value : `list`
            Each item is a name or id (looked up in the library, then in
            SIMBAD), a dictionary ``{"id": ..., "ra_deg": ..., "dec_deg":
            ...}`` for a point with known coordinates, or an object that is
            already a `Target` or `StellarObject`.

        Returns
        -------
        objects : `list`
            `Target` and `StellarObject` instances, in the order given.

        Raises
        ------
        ValueError
            If an item cannot be resolved or has no usable coordinates.
        """
        if not isinstance(value, list):
            raise ValueError("objects must be a list of names or coordinate dictionaries.")
        resolved = []
        for item in value:
            if isinstance(item, str):
                try:
                    resolved.append(wayfinder.planning.resolve_target_coordinates(item))
                except Exception as resolution_error:
                    raise ValueError(f"Could not resolve {item!r}: {resolution_error}") from resolution_error
            elif isinstance(item, dict):
                try:
                    resolved.append(
                        StellarObject(
                            id=str(item["id"]),
                            name=str(item.get("name", item["id"])),
                            ra=float(item["ra_deg"]),
                            dec=float(item["dec_deg"]),
                        )
                    )
                except KeyError as missing_key:
                    raise ValueError(
                        f"Coordinate dictionary {item!r} needs the key {missing_key}: "
                        'use {"id": ..., "ra_deg": ..., "dec_deg": ...}.'
                    ) from missing_key
            elif isinstance(item, Target | StellarObject):
                resolved.append(item)
            else:
                raise ValueError(f"Cannot use {item!r} as a sky object.")
        return resolved

    def parse_time(value: Any) -> Any:
        """Convert an ISO time string into an astropy `Time` in UTC.

        Parameters
        ----------
        value : `str` or `Time`
            ``"now"``, or an ISO 8601 time. A time with a UTC offset (for
            example ``2026-10-02T22:00:00-06:00``) is converted to UTC. A
            time with no offset is taken to be UTC.

        Returns
        -------
        time : `astropy.time.Time`
            The same instant as an astropy time.

        Raises
        ------
        ValueError
            If the string is not a valid ISO 8601 time.
        """
        if not isinstance(value, str):
            return value
        if value.strip().lower() == "now":
            return Time(datetime.now(UTC).replace(tzinfo=None))
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as parse_error:
            raise ValueError(
                f"Cannot read {value!r} as a time. Use ISO 8601, for example 2026-10-03T04:00:00Z "
                "or 2026-10-02T22:00:00-06:00."
            ) from parse_error
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return Time(parsed)

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
        "objects": resolve_sky_objects,
        "time_input": parse_time,
    }
    injected_arguments: dict[str, Callable[[], Any]] = {}
    return argument_resolvers, injected_arguments
