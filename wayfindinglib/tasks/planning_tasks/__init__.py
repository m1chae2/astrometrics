"""Purpose: Observation Planning tasks.

Description: Planning layer. Target visibility, sky catalogs, mosaics,
quality and calibration advice, and placing packages into a night. The
tasks use only Foundation code (models, astronomy calculation and
storage). They load no hardware driver and no Control code, so a night
can be planned on a computer with no telescope attached
(`Wayfinding_Library_Architecture.md`, "Planning Is Hardware-Free").
"""
