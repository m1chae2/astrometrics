"""Abstract base class for mount hardware-control protocol drivers.

Method set mirrors what
`wayfindinglib/tasks/control_tasks/hardware_operations.py` and INDI's
own `wayfindinglib/drivers/indi/mount_controller.py` already do
against `IndiInterface`, generalized so a second protocol can implement
the same surface. ST4 pulse-guiding lives here (not on the camera) per
ASCOM's `ITelescope.PulseGuide` -- today's placement on the guide camera
is a historical INDI-specific quirk, not a contract worth preserving.

Every position a mount driver reads or takes is in the mount's
current-epoch frame (JNow: the true equator and equinox of the date),
which is what INDI's ``EQUATORIAL_EOD_COORD`` holds. `hardware_operations`
converts to and from ICRS (J2000), the frame the rest of the app uses.
"""

import abc

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.drivers.interfaces.base_protocol_driver import ProtocolDriver


class MountStatus(BaseModel):
    """Mount-only subset of `indi_interface.TelescopeStatus`.

    Uses aliases to provide camelCase names for the frontend.
    """

    model_config = ConfigDict(populate_by_name=True)

    ra: str = Field(
        ..., alias="ra", description="Right Ascension in hours, in the current-epoch frame (JNow), as text."
    )
    dec: str = Field(
        ..., alias="dec", description="Declination in degrees, in the current-epoch frame (JNow), as text."
    )
    altitude: str = Field(..., alias="altitude")
    azimuth: str = Field(..., alias="azimuth")
    tracking_status: str = Field(..., alias="trackingStatus")
    connection_status: str = Field("Disconnected", alias="connectionStatus")
    target_name: str | None = Field(default=None, alias="targetName")
    pier_side: str | None = Field(
        default=None, alias="pierSide", description="``EAST`` or ``WEST``, or `None` if not reported."
    )
    parked: bool | None = Field(default=None, alias="parked", description="Whether the mount is parked.")
    track_mode: str | None = Field(
        default=None, alias="trackMode", description="The tracking rate, such as ``SIDEREAL``."
    )


class MountDriver(ProtocolDriver):
    """Abstract base for mount hardware-control protocol drivers."""

    @abc.abstractmethod
    async def get_status(self) -> MountStatus:
        """Return the mount's current coordinates, tracking, and target.

        Returns
        -------
        status : `MountStatus`
            The mount-only subset of telescope status.
        """

    @abc.abstractmethod
    async def slew(self, ra: float, dec: float) -> bool:
        """Command the mount to slew to coordinates.

        Parameters
        ----------
        ra : `float`
            Right Ascension in hours, in the current-epoch frame (JNow).
        dec : `float`
            Declination in degrees, in the current-epoch frame (JNow).

        Returns
        -------
        success : `bool`
            Whether the slew command was issued successfully.
        """

    @abc.abstractmethod
    async def sync(self, ra: float, dec: float) -> bool:
        """Sync the mount's internal coordinates without slewing.

        Parameters
        ----------
        ra : `float`
            Right Ascension in hours, in the current-epoch frame (JNow).
        dec : `float`
            Declination in degrees, in the current-epoch frame (JNow).

        Returns
        -------
        success : `bool`
            Whether the sync command was issued successfully.
        """

    @abc.abstractmethod
    async def park(self) -> bool:
        """Park the mount.

        Returns
        -------
        success : `bool`
            Whether the park command was issued successfully.
        """

    @abc.abstractmethod
    async def unpark(self) -> bool:
        """Unpark the mount.

        Returns
        -------
        success : `bool`
            Whether the unpark command was issued successfully.
        """

    @abc.abstractmethod
    async def set_tracking(self, enabled: bool) -> bool:
        """Enable or disable mount tracking.

        Returns
        -------
        success : `bool`
            Whether the tracking command was issued successfully.
        """

    @abc.abstractmethod
    async def abort_motion(self) -> bool:
        """Abort all mount motion immediately.

        Returns
        -------
        success : `bool`
            Whether the abort command was issued successfully.
        """

    @abc.abstractmethod
    async def move(self, direction: str, start: bool) -> bool:
        """Start or stop manual motor movement in a direction.

        Returns
        -------
        success : `bool`
            Whether the move command was issued successfully.
        """

    @abc.abstractmethod
    async def set_slew_rate(self, rate_index: int) -> bool:
        """Set the mount's manual-slew rate.

        Returns
        -------
        success : `bool`
            Whether the slew-rate command was issued successfully.
        """

    @abc.abstractmethod
    async def pulse_guide(self, direction: str, duration_ms: float) -> bool:
        """Send a timed ST4 guide pulse to the mount.

        Returns
        -------
        success : `bool`
            Whether the pulse-guide command was issued successfully.
        """

    @abc.abstractmethod
    async def get_observer_location(self) -> dict[str, float] | None:
        """Return the mount's configured observer location, if known.

        Returns
        -------
        location : `dict` [`str`, `float`] | `None`
            Latitude/longitude/elevation, or `None` if unavailable.
        """

    async def drain_external_pulses(self) -> list[dict]:
        """Return and clear guide pulses issued by an external commander.

        Default implementation for protocols with no way to observe
        externally-issued commands.

        Returns
        -------
        pulses : `list` [`dict`]
            Always empty for the default implementation.
        """
        return []
