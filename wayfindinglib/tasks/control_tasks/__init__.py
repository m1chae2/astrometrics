"""Purpose: Observatory Control tasks.

Description: Control layer. Hardware operation, pointing, guiding and
focus correction, safety monitoring, the enclosure and the safe-state
sequence, and the reading of the incumbent software's session logs.
These tasks work with no plan and no session present, so they import
nothing from `planning_tasks` or `execution_tasks`
(`Wayfinding_Library_Architecture.md`, Design Invariants 2 and 3).
"""
