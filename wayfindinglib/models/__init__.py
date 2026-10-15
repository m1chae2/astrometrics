"""Purpose: Init file for Wayfinding domain models.

Description: Foundation-layer pure Pydantic schema, no behavior
(`Wayfinding_Library_Architecture.md` Section 2.1, Figure 1). Domain
models import nothing from `tasks/`, `api/`, `drivers/` or `analytics/`
-- Section 2.1, rule 1, "Foundation independence". Calculations on these
models live in `analytics/`, which imports the models, never the
reverse.
"""
