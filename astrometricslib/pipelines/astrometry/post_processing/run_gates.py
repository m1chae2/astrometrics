"""Builds the gate record for one astrometry run.

Astrometry finds the stars in an image, works out where the image points
(a "plate solve") and names the stars by looking them up in online catalogs.
Three checks say whether the run can be trusted: were any stars found, did the
plate solve succeed, and did the catalog lookups work.

Each check is returned as a `GateResult`. The plate-solve gate is built by the
runner as before; the other two are built here. A catalog lookup that was
never attempted is ``not_checked``; it is not a pass.
"""

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.quality_summary import AstrometryPipelineQualityMetrics

SOURCE_DETECTION_GATE_NAME = "source_detection"
CATALOG_LOOKUP_GATE_NAME = "catalog_lookup"

# The share of catalog lookups that may fail before the star names cannot be
# trusted. A design estimate: the first failures usually come from one slow
# or unreachable service, and half failing means most stars went unnamed.
MAXIMUM_FAILED_LOOKUP_FRACTION = 0.5


def astrometry_run_gates(metrics: AstrometryPipelineQualityMetrics) -> list[GateResult]:
    """Build the source-detection and catalog-lookup gates.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.

    Returns
    -------
    gates : `list` [`GateResult`]
        The ``source_detection`` and ``catalog_lookup`` gates.
    """
    gates: list[GateResult] = []
    if metrics.sources_detected <= 0:
        gates.append(
            failed_gate(
                SOURCE_DETECTION_GATE_NAME,
                "no stars were detected in the image",
                float(metrics.sources_detected),
                1.0,
            )
        )
    else:
        gates.append(passed_gate(SOURCE_DETECTION_GATE_NAME, float(metrics.sources_detected), 1.0))

    lookup_source = (
        f"under {MAXIMUM_FAILED_LOOKUP_FRACTION:.0%} of lookups failed and the circuit "
        "breaker did not trip (design estimate)"
    )
    attempted = metrics.remote_catalog_queries_attempted
    failed = metrics.remote_catalog_queries_failed
    if metrics.remote_catalog_circuit_breaker_tripped:
        gates.append(
            failed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                "catalog lookups kept failing, so the service was dropped and some stars are unnamed",
                float(failed),
                limit_source=lookup_source,
            )
        )
    elif attempted == 0:
        gates.append(
            unchecked_gate(CATALOG_LOOKUP_GATE_NAME, "no catalog lookup was attempted", lookup_source)
        )
    elif failed / attempted >= MAXIMUM_FAILED_LOOKUP_FRACTION:
        gates.append(
            failed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                f"{failed} of {attempted} catalog lookup(s) failed; many stars may be unnamed",
                failed / attempted,
                MAXIMUM_FAILED_LOOKUP_FRACTION,
                lookup_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                failed / attempted,
                MAXIMUM_FAILED_LOOKUP_FRACTION,
                lookup_source,
                f"{failed} of {attempted} catalog lookup(s) failed",
            )
        )
    return gates
