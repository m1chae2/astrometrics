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
    """No parameter name is declared in both libraries."""
    wayfinding = set(collect_parameters("wayfindinglib").names())
    astrometrics = set(collect_parameters("astrometricslib").names())
    assert not wayfinding & astrometrics
