"""Purpose: Init file for the quality-statement domain model group.

Description: The one vocabulary that Control (the per-night view) and
Planning (the per-target view) share for judging quality: statements,
the rule table's row shape, efficiency and the loss account, and the
per-capture-setting summary. Foundation-layer pure Pydantic schema, no
behavior beyond field checks (`Wayfinding_Library_Architecture.md`
Section 2.1). The models import `astrometricslib` only from its package
root. Nothing here is ever stored: statements are recomputed each time
they are read.
"""
