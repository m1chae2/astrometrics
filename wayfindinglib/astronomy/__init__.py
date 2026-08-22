"""Purpose: Init file for Wayfinding astronomy calculation.

Description: Pure coordinate transforms, altitude/azimuth and hour-angle
computation, and solar-altitude sampling -- calculation true regardless
of intent, with no scheduling policy attached
(`Wayfinding_Library_Architecture.md` §2.1). `night_window.py`'s
twilight bracket and `visibility_tasks.py`'s floor-and-obstruction
policy consume this module rather than duplicating its math; they are
Observation Planning's concern, not this Foundation module's.
"""
