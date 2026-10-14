"""Purpose: Init file for Wayfinding domain models.

Description: Foundation-layer pure Pydantic schema, no behavior
(`Wayfinding_Library_Architecture.md`, Figure 1). Domain models import
nothing from `tasks/`, `api/`, `drivers/` or `analytics/` -- Design
Invariant 1 (Foundation Independence). Calculations on these models live
in `analytics/`, which imports the models, never the reverse.
"""
