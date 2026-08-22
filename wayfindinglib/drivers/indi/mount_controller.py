"""Mount controller for INDI devices.

Handles slewing, parking, and tracking.
"""

import logging

import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.time import Time

from .property_wait import wait_for_switch_state
from .pyindi_compatibility import PyIndi

logger = logging.getLogger(__name__)


class MountController:
    """Manages telescope mount operations via INDI."""

    def __init__(self, client):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.client = client
        self.config = client.config

    def slew(self, telescope, ra: float, dec: float) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Slews the telescope to the specified coordinates.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        ra : float
            Target right ascension, in hours.
        dec : float
            Target declination, in degrees.

        Returns
        -------
        bool
            True if the slew command was sent, False otherwise.
        """
        if not telescope:
            return False

        # ENFORCE: Altitude and hour-angle constraints
        self.validate_altitude_limits(telescope, ra, dec)
        self.validate_hour_angle_limits(telescope, ra)

        # Auto-unpark if necessary
        self.unpark(telescope)

        # Ensure we are in TRACK mode
        self._set_coord_mode(telescope, "TRACK")

        ra_dec = telescope.getNumber("EQUATORIAL_EOD_COORD")
        if not ra_dec:
            ra_dec = telescope.getNumber("HORIZONTAL_COORD")

        if ra_dec:
            ra_dec[0].value = ra
            ra_dec[1].value = dec
            self.client.sendNewNumber(ra_dec)
            return True
        return False

    def park(self, telescope, timeout: float = 5.0) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Parks the telescope, waiting for the driver to confirm the change.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        timeout : float
            Maximum number of seconds to wait for the driver to confirm
            the park switch state.

        Returns
        -------
        bool
            True if the park command was sent and confirmed, False
            otherwise.
        """
        if not telescope:
            return False
        park_switch = telescope.getSwitch("TELESCOPE_PARK") or telescope.getSwitch("PARK")
        if not park_switch:
            return False

        for i in range(len(park_switch)):
            name = park_switch[i].getName().upper()
            if "PARK" in name and "UNPARK" not in name:
                park_switch[i].s = PyIndi.ISS_ON
            else:
                park_switch[i].s = PyIndi.ISS_OFF

        self.client.sendNewSwitch(park_switch)
        return wait_for_switch_state(
            telescope, "TELESCOPE_PARK", "PARK", PyIndi.ISS_ON, timeout=timeout, fallback_name="PARK"
        )

    def unpark(self, telescope, timeout: float = 5.0) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Unparks the telescope, waiting for the driver to confirm the change.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        timeout : float
            Maximum number of seconds to wait for the driver to confirm
            the unpark switch state.

        Returns
        -------
        bool
            True if already unparked or the unpark command was confirmed,
            False otherwise.
        """
        if not telescope:
            return False
        park_switch = telescope.getSwitch("TELESCOPE_PARK") or telescope.getSwitch("PARK")
        if not park_switch:
            return False

        is_parked = False
        unpark_idx = -1
        for i in range(len(park_switch)):
            name = park_switch[i].getName().upper()
            if "PARK" in name and "UNPARK" not in name:
                if park_switch[i].s == PyIndi.ISS_ON:
                    is_parked = True
            elif "UNPARK" in name:
                unpark_idx = i

        if not is_parked:
            return True
        if unpark_idx == -1:
            return False

        for i in range(len(park_switch)):
            park_switch[i].s = PyIndi.ISS_OFF
        park_switch[unpark_idx].s = PyIndi.ISS_ON
        self.client.sendNewSwitch(park_switch)
        return wait_for_switch_state(
            telescope, "TELESCOPE_PARK", "UNPARK", PyIndi.ISS_ON, timeout=timeout, fallback_name="PARK"
        )

    def set_tracking(self, telescope, enabled: bool, timeout: float = 5.0) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Set the tracking state and confirm the change with the driver.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        enabled : bool
            True to enable tracking, False to disable it.
        timeout : float
            Maximum number of seconds to wait for the driver to confirm
            the tracking state.

        Returns
        -------
        bool
            True if the tracking state was set and confirmed, False
            otherwise.
        """
        if not telescope:
            return False
        track_switch = telescope.getSwitch("TELESCOPE_TRACK_STATE")
        if not track_switch:
            return False

        track_on_idx = -1
        track_off_idx = -1
        for i in range(len(track_switch)):
            name = track_switch[i].getName().upper()
            if name == "TRACK_ON" or name == "ON":
                track_on_idx = i
            elif name == "TRACK_OFF" or name == "OFF" or name == "IDLE":
                track_off_idx = i

        # Fallback if exact match failed
        if track_on_idx == -1 or track_off_idx == -1:
            for i in range(len(track_switch)):
                name = track_switch[i].getName().upper()
                if "ON" in name and track_on_idx == -1:
                    track_on_idx = i
                elif ("OFF" in name or "IDLE" in name) and track_off_idx == -1:
                    track_off_idx = i

        if track_on_idx == -1 or track_off_idx == -1:
            return False

        track_switch[track_on_idx].s = PyIndi.ISS_ON if enabled else PyIndi.ISS_OFF
        track_switch[track_off_idx].s = PyIndi.ISS_OFF if enabled else PyIndi.ISS_ON
        self.client.sendNewSwitch(track_switch)

        # Poll for the actual matched element name (which may be a
        # fallback like "ON"/"IDLE" rather than the literal
        # "TRACK_ON"/"TRACK_OFF").
        expected_idx = track_on_idx if enabled else track_off_idx
        expected_name = track_switch[expected_idx].getName()
        return wait_for_switch_state(
            telescope, "TELESCOPE_TRACK_STATE", expected_name, PyIndi.ISS_ON, timeout=timeout
        )

    def _resolve_altitude_envelope(self) -> tuple[float, float, bool]:
        """Resolve the altitude envelope slews are validated against.

        Prefers the active per-rig `Telescope`'s configured envelope
        when one is configured; falls back to the global
        `[Observatory.Constraints]` section otherwise, per
        `Wayfinding_Library_Architecture.md`
        §2.2.2's "Documented Safety Fallback" invariant -- a rig that
        has not yet been given its own section does not silently
        change behavior. Reading a `Telescope` model here is legal
        (models are Foundation, same as this driver); the observer
        position this envelope is evaluated at, below, is a separate,
        deliberately deferred concern (§2.5.2).

        Returns
        -------
        min_altitude_deg, max_altitude_deg, limits_enabled : `tuple`
            The envelope bounds, and whether limit checking is enabled
            for the active rig (always `True` for the global fallback,
            which has no such toggle).
        """
        from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog

        active_telescope = get_equipment_catalog(self.config).active_telescope()
        if active_telescope is not None:
            return (
                active_telescope.min_altitude_deg,
                active_telescope.max_altitude_deg,
                active_telescope.altitude_limits_enabled,
            )
        return self.config.get_min_altitude(), self.config.get_max_altitude(), True

    def validate_altitude_limits(self, telescope, ra: float, dec: float):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Calculate and validates altitude for target coordinates.

        REQ: OBS-1.1

        Raises
        ------
        AstrometryHardwareError
            If the target altitude falls outside the configured
            minimum/maximum safe operating envelope.
        """
        min_altitude, max_altitude, limits_enabled = self._resolve_altitude_envelope()
        if not limits_enabled:
            return

        geographic_coordinates = telescope.getNumber("GEOGRAPHIC_COORD")
        if not geographic_coordinates:
            logger.warning("Cannot validate altitude limits (Mount GEOGRAPHIC_COORD property missing)")
            return

        latitude = geographic_coordinates[0].value
        longitude = geographic_coordinates[1].value
        elevation = geographic_coordinates[2].value if len(geographic_coordinates) > 2 else 0

        observation_time = Time.now()
        location = EarthLocation(lat=latitude * u.deg, lon=longitude * u.deg, height=elevation * u.m)

        from wayfindinglib.skylib.coordinate_operations import compute_altaz

        # ra is in Hours (INDI convention); compute_altaz's contract
        # is degrees.
        target_altitude, _target_azimuth = compute_altaz(ra * 15.0, dec, location, observation_time)

        if target_altitude < min_altitude or target_altitude > max_altitude:
            from wayfindinglib import AstrometryHardwareError

            raise AstrometryHardwareError(
                f"Slew rejected: Target altitude ({target_altitude:.1f}°) is outside safe operating "
                f"envelope ({min_altitude}° to {max_altitude}°)"
            )

    def _resolve_hour_angle_envelope(self) -> tuple[float, bool]:
        """Resolve the hour-angle envelope slews are validated against.

        Reads the active per-rig `Telescope`'s configured envelope;
        disabled with no active telescope, since
        `hour_angle_limits_enabled` is a
        per-rig field with no documented global fallback (unlike
        altitude's `[Observatory.Constraints]` section).

        Returns
        -------
        max_hour_angle_hours, limits_enabled : `tuple`
            The envelope bound, and whether limit checking is enabled
            for the active rig.
        """
        from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog

        active_telescope = get_equipment_catalog(self.config).active_telescope()
        if active_telescope is not None:
            return active_telescope.max_hour_angle_hours, active_telescope.hour_angle_limits_enabled
        return 0.0, False

    def validate_hour_angle_limits(self, telescope, ra: float):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Calculate and validate hour angle for a target right ascension.

        REQ: OBS-1.1

        Raises
        ------
        AstrometryHardwareError
            If the target's hour angle falls outside the configured
            maximum east/west bound.
        """
        max_hour_angle_hours, limits_enabled = self._resolve_hour_angle_envelope()
        if not limits_enabled:
            return

        geographic_coordinates = telescope.getNumber("GEOGRAPHIC_COORD")
        if not geographic_coordinates:
            logger.warning("Cannot validate hour angle limits (Mount GEOGRAPHIC_COORD property missing)")
            return

        longitude = geographic_coordinates[1].value

        observation_time = Time.now()
        local_sidereal_time = observation_time.sidereal_time("mean", longitude=longitude * u.deg).hour

        hour_angle = local_sidereal_time - ra
        hour_angle = ((hour_angle + 12.0) % 24.0) - 12.0

        if abs(hour_angle) > max_hour_angle_hours:
            from wayfindinglib import AstrometryHardwareError

            raise AstrometryHardwareError(
                f"Slew rejected: Target hour angle ({hour_angle:.2f}h) is outside safe operating "
                f"envelope (±{max_hour_angle_hours}h)"
            )

    def sync_coordinates(self, telescope, ra: float, dec: float) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Sync the telescope to the specified coordinates.

        This is a recalibration, not a movement. Uses ON_COORD_SET = SYNC.
        Fire-and-forget: sync is an instantaneous re-registration of the
        driver's internal position, not physical motion, so it doesn't
        need the wait-for-confirmation treatment park/unpark/set_tracking
        use.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        ra : float
            Right ascension, in hours, to sync to.
        dec : float
            Declination, in degrees, to sync to.

        Returns
        -------
        bool
            True if the sync command was sent, False otherwise.
        """
        if not telescope:
            return False

        self._set_coord_mode(telescope, "SYNC")

        ra_dec = telescope.getNumber("EQUATORIAL_EOD_COORD")
        if ra_dec:
            ra_dec[0].value = ra
            ra_dec[1].value = dec
            self.client.sendNewNumber(ra_dec)
            return True
        return False

    def move(self, telescope, direction: str, start: bool = True) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Start or stop manual jogging in a direction.

        Fire-and-forget: this drives interactive jog controls (mouse down/up),
        where blocking on network confirmation for every press/release would
        make manual control feel laggy. Unlike park/unpark/set_tracking, no
        wait-for-confirmation is applied here.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        direction : str
            One of 'N', 'S', 'E', 'W', 'NW', 'NE', 'SW', 'SE', or 'STOP'.
        start : bool
            True to start jogging in the given direction, False to stop.

        Returns
        -------
        bool
            True if a motion switch was sent, False otherwise.
        """
        if not telescope:
            return False

        if direction == "STOP":
            north_south_motion = telescope.getSwitch("TELESCOPE_MOTION_NS")
            if north_south_motion:
                for i in range(len(north_south_motion)):
                    north_south_motion[i].s = PyIndi.ISS_OFF
                self.client.sendNewSwitch(north_south_motion)
            west_east_motion = telescope.getSwitch("TELESCOPE_MOTION_WE")
            if west_east_motion:
                for i in range(len(west_east_motion)):
                    west_east_motion[i].s = PyIndi.ISS_OFF
                self.client.sendNewSwitch(west_east_motion)
            return True

        success = False
        if any(direction_char in direction for direction_char in ("N", "S")):
            north_south_motion = telescope.getSwitch("TELESCOPE_MOTION_NS")
            if north_south_motion:
                for i in range(len(north_south_motion)):
                    name = north_south_motion[i].getName()
                    if "N" in direction and name == "MOTION_NORTH":
                        north_south_motion[i].s = PyIndi.ISS_ON if start else PyIndi.ISS_OFF
                    elif "S" in direction and name == "MOTION_SOUTH":
                        north_south_motion[i].s = PyIndi.ISS_ON if start else PyIndi.ISS_OFF
                    else:
                        north_south_motion[i].s = PyIndi.ISS_OFF
                self.client.sendNewSwitch(north_south_motion)
                success = True

        if any(direction_char in direction for direction_char in ("E", "W")):
            west_east_motion = telescope.getSwitch("TELESCOPE_MOTION_WE")
            if west_east_motion:
                for i in range(len(west_east_motion)):
                    name = west_east_motion[i].getName()
                    if "E" in direction and name == "MOTION_EAST":
                        west_east_motion[i].s = PyIndi.ISS_ON if start else PyIndi.ISS_OFF
                    elif "W" in direction and name == "MOTION_WEST":
                        west_east_motion[i].s = PyIndi.ISS_ON if start else PyIndi.ISS_OFF
                    else:
                        west_east_motion[i].s = PyIndi.ISS_OFF
                self.client.sendNewSwitch(west_east_motion)
                success = True

        return success

    def abort(self, telescope) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Abort all telescope mount motion immediately.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.

        Returns
        -------
        bool
            True if the abort or stop motion command succeeded.
        """
        if not telescope:
            return False

        abort_switch = telescope.getSwitch("TELESCOPE_ABORT_MOTION") or telescope.getSwitch("ABORT")
        if abort_switch:
            for i in range(len(abort_switch)):
                abort_switch[i].s = PyIndi.ISS_ON
            self.client.sendNewSwitch(abort_switch)
            return True
        return self.move(telescope, "STOP", False)

    def _set_coord_mode(self, telescope, mode: str) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Set ON_COORD_SET mode (SLEW, TRACK, SYNC) by name or label.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        mode : str
            Target coordinate-set mode name (e.g. 'SLEW', 'TRACK', 'SYNC').

        Returns
        -------
        bool
            True if a matching switch element was found and sent, False
            otherwise.
        """
        try:
            coordinate_set_switch = telescope.getSwitch("ON_COORD_SET")
            if not coordinate_set_switch:
                return False

            target_name = mode.upper()
            found = False
            for i in range(len(coordinate_set_switch)):
                coordinate_set_switch[i].s = PyIndi.ISS_OFF
            for i in range(len(coordinate_set_switch)):
                element_name = coordinate_set_switch[i].getName().upper()
                element_label = coordinate_set_switch[i].getLabel().upper()
                if element_name == target_name or element_label == target_name:
                    coordinate_set_switch[i].s = PyIndi.ISS_ON
                    found = True
                    break

            if found:
                self.client.sendNewSwitch(coordinate_set_switch)
                return True
        except Exception as coord_mode_error:
            logger.warning("Failed to set coord mode %s: %s", mode, coord_mode_error)
        return False
