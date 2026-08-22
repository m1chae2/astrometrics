"""Purpose: Observatory Library Imports Initialization.

Description: Package initialization for `observatorylib`. Hardware
operations, alignment operations, and coordinate parsing relocated to
`wayfindinglib.tasks.control_tasks`/`astrometricslib.utilities
.coordinate_parsing` during the three-function redesign's Observatory
Control milestone; only `equipment_configuration` (still composed by
`wayfindinglib.tasks.control_tasks.equipment_activation` for its
`CameraProfile`/`EquipmentConfiguration` dict shapes) remains here.
"""
