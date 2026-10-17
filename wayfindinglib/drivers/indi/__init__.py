"""Purpose: The INDI hardware drivers.

Description: Control layer. INDI (Instrument Neutral Distributed
Interface) is the protocol the mount, cameras, focuser, filter wheel,
enclosure, power box and weather sensors speak. The ``*_controller.py``
modules read and command one device type each over a live ``PyIndi``
connection; the ``*_driver.py`` modules adapt them to the per-device
interfaces in `wayfindinglib/drivers/interfaces/`. Every module imports
``PyIndi`` through `pyindi_compatibility.py`, so the package still loads
where INDI is not installed. Planning and the Foundation never import
this package (`Wayfinding_Library_Architecture.md` Section 4.4,
"Planning is hardware-free").
"""
