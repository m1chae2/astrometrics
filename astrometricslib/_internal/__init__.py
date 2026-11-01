"""Purpose: The private names of `astrometricslib`, in logical groups.

Description: Only `astrometricslib` itself, and the tests of either library,
may import from this package. Each module holds one group. A group module
imports its names from the module where the code lives today, so moving the
code later changes that one module only. Code outside the library uses the
public modules instead.
"""
