"""Purpose: The task functions behind the three sub-APIs.

Description: One sub-package per function of the architecture:
`control_tasks` (operate the observatory), `planning_tasks` (decide what
to observe) and `execution_tasks` (run and recover a session). Control
and Planning never import each other, and only Execution and the
composition root import Execution (`Wayfinding_Library_Architecture.md`,
Figure 1).
"""
