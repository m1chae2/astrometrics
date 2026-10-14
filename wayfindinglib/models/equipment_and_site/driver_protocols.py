"""Purpose: The protocol names a device can be configured with.

Description: Each device in the equipment catalog names the protocol that
drives it, such as ``"indi"`` for a mount on an INDI server or ``"phd2"``
for guiding through PHD2. This module lists the valid names for each
device type as plain strings.

The names live here, in the Foundation, so two layers can share them
without one importing the other. The equipment catalog reader checks a
configured name against these sets when it loads the catalog, and never
has to load a hardware driver to do so. The driver registry
(`drivers/interfaces/registry.py`) keys its driver classes by the same
names. A test checks that each set here equals the keys of its registry,
so the two cannot drift apart.
"""

INDI_PROTOCOL = "indi"
"""Devices on an INDI server (Instrument Neutral Distributed Interface)."""

PHD2_GUIDING_PROTOCOL = "phd2"
"""Guiding run by the PHD2 program."""

INTERNAL_GUIDING_PROTOCOL = "internal"
"""Guiding run by KStars/Ekos, which pulses the mount itself over INDI."""

SIMULATOR_GUIDING_PROTOCOL = "simulator"
"""A simulated guide loop, for testing without hardware."""

STELLARMATE_TRANSFER_DRIVER = "stellarmate"
"""File transfer from a StellarMate telescope computer."""

MOUNT_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid values of a telescope's ``mount_protocol``."""

CAMERA_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid values of a camera's ``protocol``."""

FOCUSER_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid values of a telescope's ``focuser_protocol``."""

FILTER_WHEEL_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid values of a telescope's ``filter_wheel_protocol``."""

ENCLOSURE_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid values of an enclosure's ``protocol``."""

SWITCH_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid protocols for a power switch."""

WEATHER_PROTOCOLS = frozenset({INDI_PROTOCOL})
"""Valid protocols for a weather sensor."""

GUIDING_PROTOCOLS = frozenset({PHD2_GUIDING_PROTOCOL, INTERNAL_GUIDING_PROTOCOL, SIMULATOR_GUIDING_PROTOCOL})
"""Valid values of a telescope's ``guiding_protocol``."""

REMOTE_TRANSFER_DRIVERS = frozenset({STELLARMATE_TRANSFER_DRIVER})
"""Valid names of a remote file-transfer driver."""
