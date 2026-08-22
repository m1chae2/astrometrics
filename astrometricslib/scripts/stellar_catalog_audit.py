"""Demo script demonstrating stellar catalog quality auditing.

Queries database audit log history and statistics metrics to audit
overall database health.
"""

import argparse

from astrometricslib import Astrometrics


def run_catalog_audit() -> None:
    """Parse CLI arguments and print a stellar catalog audit report.

    Retrieves stellar catalog database audit metrics and prints a
    verification report of the scientific library, warning if the
    spectral coverage falls below the requested minimum.

    Raises
    ------
    SystemExit
        Raised with exit code 1 if the catalog audit fails.
    """
    parser = argparse.ArgumentParser(description="Astrometrics Stellar Catalog Audit Tool")
    parser.add_argument(
        "--min-spectral-coverage",
        type=float,
        default=0.0,
        help="Optional minimum expected spectral coverage percentage.",
    )

    args = parser.parse_args()

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()

    print("Running Stellar Catalog Quality Auditing (Use Case 8.2)...")
    try:
        audit_results = astrometrics.stars.get_audit()

        print("\n==========================================")
        print("STELLAR CATALOG AUDIT REPORT")
        print("==========================================")
        print(f"Total Discovered Stellar Objects:   {audit_results.get('total_objects')}")
        print(f"Identified (Named) Star Profiles:   {audit_results.get('identified_objects')}")
        print(f"Spectral Profile Coverage:          {audit_results.get('spectral_coverage')}%")
        print(f"Photometric Lightcurve Coverage:    {audit_results.get('photometric_coverage')}%")

        stats = audit_results.get("stats", {})
        print("\nBreakdown Statistics:")
        print(f"  Named Stars:                      {stats.get('names')}")
        print(f"  Stars with Spectral Data:         {stats.get('spectral')}")
        print(f"  Stars with Magnitude Data:        {stats.get('magnitude')}")
        print("==========================================")

        # Basic verification check
        min_expected = getattr(args, "min_spectral_coverage", 0.0)
        actual_cov = audit_results.get("spectral_coverage", 0.0)
        if actual_cov < min_expected:
            print(
                f"WARNING: Spectral coverage ({actual_cov}%) is below expected threshold ({min_expected}%)."
            )
        else:
            print("Database status: CONSISTENT / NORMAL")

    except Exception as err:
        print(f"Error executing catalog audit: {err}")
        raise SystemExit(1) from err


if __name__ == "__main__":
    run_catalog_audit()
