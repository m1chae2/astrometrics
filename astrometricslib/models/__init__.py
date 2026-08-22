"""Pure domain data schemas for astrometricslib.

Modules under `models/` define Pydantic data classes only -- zero
imports of `astrometricslib.tasks` or `astrometricslib.api`. Algorithmic
orchestration lives in `astrometricslib.tasks`; domain-astrometrics classes
live in `astrometricslib.api`.
"""
