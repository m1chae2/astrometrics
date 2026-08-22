"""Algorithmic orchestration tasks for astrometricslib.

Modules under `tasks/` operate on `astrometricslib.models` data classes
and delegate any disk/network/database I/O to
`astrometricslib.data_access`. Domain-astrometrics classes in
`astrometricslib.api` delegate their work to `tasks/`.
"""
