"""Algorithmic leaves shared across the target/stellar/moving-object domains.

Neither `saturation_analysis` (target-quality checks used by stellar
pipelines) nor `source_detection_shared` (point-source detection used
by target-, stellar-, and moving-object-domain code) is really
domain-specific -- their prior location under `target_tasks`/
`stellar_tasks` respectively was what produced the bidirectional
`target_tasks` <-> `stellar_tasks` import cycle.
"""
