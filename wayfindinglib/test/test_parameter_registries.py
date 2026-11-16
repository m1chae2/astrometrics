"""Tests that both parameter registries collect and do not overlap."""

import logging
import time

from astrometricslib.parameters import collect_parameters


def test_both_registries_collect() -> None:
    """Both libraries' parameters are collected, and the time is reported."""
    started = time.perf_counter()
    wayfinding = collect_parameters("wayfindinglib")
    elapsed = time.perf_counter() - started
    astrometrics = collect_parameters("astrometricslib")
    logging.getLogger(__name__).info(
        "wayfindinglib collection took %.2f s (%d parameters)", elapsed, len(wayfinding.names())
    )
    assert isinstance(wayfinding.names(), tuple)
    assert isinstance(astrometrics.names(), tuple)


def test_no_parameter_name_is_in_both_registries() -> None:
    """No parameter name is declared twice, once in each library.

    A declaration wayfindinglib imports from astrometricslib's public module
    (the spectrum match limits, the guide cycle) shows up in both registries,
    but it is one object, declared once; two different declarations with one
    name would be a clash.
    """
    wayfinding = collect_parameters("wayfindinglib")
    astrometrics = collect_parameters("astrometricslib")
    shared = set(wayfinding.names()) & set(astrometrics.names())
    clashes = sorted(name for name in shared if wayfinding.get(name) is not astrometrics.get(name))
    assert not clashes
