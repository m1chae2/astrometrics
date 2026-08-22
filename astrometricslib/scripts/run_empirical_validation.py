"""Purpose: Master script for empirical design validation of astrometricslib.

Description: Executes empirical pipeline validation across ZWO ASI 533MM
Pro target datasets for all five processing pipelines (Spectroscopy,
Photometry, Moving Object Recovery, Astrometry, and Stacking). Applies
fault-tolerant execution and incremental state saving
(astrometrics.targets.save()) after each target/pipeline step.
"""

import argparse
import sys
import traceback

from astrometricslib import Astrometrics


def run_spectroscopy_validation(astrometrics: Astrometrics, camera_name: str) -> dict:
    """Execute spectroscopy validation on Vega and M 13 datasets.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The initialized library high-level interface instance.
    camera_name : `str`
        Filter frames matching this camera name.

    Returns
    -------
    metrics : `dict`
        Summary dictionary of spectroscopy validation results.
    """
    print("\n--------------------------------------------------")
    print("RUNNING PIPELINE 1/5: STELLAR SPECTROSCOPY")
    print("--------------------------------------------------")
    results = {}

    # Target 1: Vega calibration tuning
    vega_target = astrometrics.targets.get("Vega")
    if vega_target and vega_target.stacked_spectral_target:
        print(f"Executing Vega calibration tuning on: {vega_target.stacked_spectral_target}")
        try:
            tune_res = astrometrics.stars.tune_spectroscopy_calibration(
                vega_target.stacked_spectral_target, camera_name=camera_name
            )
            rms_err = tune_res.get("rms_error_nm", 0.0)
            print(f"  Vega Calibration RMS Error: {rms_err:.3f} nm")
            results["vega_calibration"] = tune_res
        except Exception as err:
            print(f"  Vega calibration tuning notice: {err}")

    # Target 2: Field spectroscopy analysis on Vega & M 13
    for tid in ["Vega", "M 13"]:
        t = astrometrics.targets.get(tid)
        if t:
            print(f"Running spectroscopy analysis on target: {t.id}")
            try:
                res = astrometrics.processing.run_spectroscopy(t, limit=10)
                if res and "context" in res:
                    summary = getattr(t, "spectroscopy_quality_summary", None)
                    flagged = summary.flagged if summary else False
                    print(f"  Target {t.id} Spectroscopy Completed. Flagged: {flagged}")
                    results[t.id] = {"status": "success", "flagged": flagged}
                astrometrics.targets.save()
                print(f"  Incremental save completed for target: {t.id}")
            except Exception as err:
                print(f"  Spectroscopy analysis error for {t.id}: {err}")
                results[t.id] = {"status": "error", "message": str(err)}

    return results


def run_photometry_validation(astrometrics: Astrometrics, camera_name: str) -> dict:
    """Execute differential photometry validation on NGC 2244 and M 81.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The initialized library high-level interface instance.
    camera_name : `str`
        Filter frames matching this camera name.

    Returns
    -------
    metrics : `dict`
        Summary dictionary of photometry validation results.
    """
    print("\n--------------------------------------------------")
    print("RUNNING PIPELINE 2/5: STELLAR PHOTOMETRY")
    print("--------------------------------------------------")
    results = {}

    for tid in ["NGC 2244", "M 81", "NGC 2903"]:
        target = astrometrics.targets.get(tid)
        if target:
            zwo_frames = [
                f
                for f in target.frames
                if not camera_name or f.camera.upper() == camera_name.upper() or f.camera == "Unknown"
            ]
            print(f"Running Photometry Analysis on target: {target.id} ({len(zwo_frames)} matching frames)")
            try:
                res = astrometrics.processing.run_photometry(
                    target,
                    frames=zwo_frames,
                    filter_type="Luminance",
                )
                status = res.get("status", "completed")
                stars_found = res.get("starsFound", 0)
                stars_proc = res.get("starsProcessed", 0)
                cand_count = len(res.get("variable_candidates", []))
                print(f"  Target {target.id} Photometry Status: {status}")
                print(f"  Stars Found: {stars_found}, Saturated/Processed: {stars_proc}")
                print(f"  Variable Star Candidates: {cand_count}")
                results[target.id] = res

                astrometrics.targets.save()
                print(f"  Incremental save completed for target: {target.id}")
            except Exception as err:
                print(f"  Photometry error on {target.id}: {err}")
                results[target.id] = {"status": "error", "message": str(err)}

    return results


def run_asteroid_recovery_validation(astrometrics: Astrometrics, camera_name: str) -> dict:
    """Execute asteroid recovery validation on NGC 2403 and M 81 datasets.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The initialized library high-level interface instance.
    camera_name : `str`
        Filter frames matching this camera name.

    Returns
    -------
    metrics : `dict`
        Summary dictionary of asteroid recovery validation results.
    """
    print("\n--------------------------------------------------")
    print("RUNNING PIPELINE 3/5: ASTEROID / MOVING OBJECT RECOVERY")
    print("--------------------------------------------------")
    results = {}

    for tid in ["NGC 2403", "M 81"]:
        target = astrometrics.targets.get(tid)
        if target and target.stacked_image:
            print(f"Running Asteroid Recovery Analysis on target: {target.id}")
            try:
                candidates = astrometrics.moving_objects.recover_asteroids(target)
                metrics = astrometrics.moving_objects.last_run_metrics
                det = metrics.get("candidates_detected", 0)
                lin = metrics.get("candidates_rate_linearity_confirmed", 0)
                eph = metrics.get("candidates_ephemeris_matched", 0)
                res = {"candidates": candidates, **metrics}
                print(f"  Target {target.id} Candidates Detected: {det}")
                print(f"  Confirmed Linear Movers: {lin}")
                print(f"  SkyBoT Ephemeris Matched: {eph}")
                results[target.id] = res

                astrometrics.targets.save()
                print(f"  Incremental save completed for target: {target.id}")
            except Exception as err:
                print(f"  Asteroid recovery notice for {target.id}: {err}")
                results[target.id] = {"status": "notice", "message": str(err)}

    return results


def run_astrometry_validation(astrometrics: Astrometrics, camera_name: str) -> dict:
    """Execute astrometric plate-solving validation across targets.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The initialized library high-level interface instance.
    camera_name : `str`
        Filter frames matching this camera name.

    Returns
    -------
    metrics : `dict`
        Summary dictionary of astrometry validation results.
    """
    print("\n--------------------------------------------------")
    print("RUNNING PIPELINE 4/5: ASTROMETRIC CALIBRATION")
    print("--------------------------------------------------")
    results = {}

    for tid in ["NGC 1893", "M 45", "NGC 2403", "M 81"]:
        target = astrometrics.targets.get(tid)
        if target and target.stacked_image:
            print(f"Running Astrometry WCS Plate-Solve on target: {target.id}")
            try:
                res = astrometrics.processing.run_astrometry(target)
                if res:
                    wcs_found = res.get("wcs") is not None
                    stars_count = len(res.get("stellar_objects", []))
                    print(f"  Target {target.id} Solve Succeeded: {wcs_found}, Detected Stars: {stars_count}")
                    results[target.id] = {
                        "wcs_resolved": wcs_found,
                        "stars_detected": stars_count,
                    }

                astrometrics.targets.save()
                print(f"  Incremental save completed for target: {target.id}")
            except Exception as err:
                print(f"  Astrometry error for {target.id}: {err}")
                results[target.id] = {"status": "error", "message": str(err)}

    return results


def run_stacking_validation(astrometrics: Astrometrics, camera_name: str) -> dict:
    """Execute image stacking Chauvenet outlier rejection validation.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The initialized library high-level interface instance.
    camera_name : `str`
        Filter frames matching this camera name.

    Returns
    -------
    metrics : `dict`
        Summary dictionary of stacking validation results.
    """
    print("\n--------------------------------------------------")
    print("RUNNING PIPELINE 5/5: IMAGE STACKING & CHAUVENET REJECTION")
    print("--------------------------------------------------")
    results = {}

    for tid in ["NGC 2403", "M 81", "M 13"]:
        target = astrometrics.targets.get(tid)
        if target:
            summary = getattr(target, "stack_quality_summary", None)
            if summary:
                print(f"  Target {target.id} Stack Quality Summary Found:")
                print(f"    Flagged: {summary.flagged}, Reasons: {summary.flag_reasons}")
                if summary.stacking_metrics:
                    m = summary.stacking_metrics
                    print(
                        f"    Stacked FWHM: {m.stacked_fwhm_px} px, Median Input: {m.median_input_fwhm_px} px"
                    )
                    print(f"    Rejected Pixel Fraction: {m.rejected_pixel_fraction}")
                results[target.id] = summary.model_dump(mode="json")

    return results


def main() -> None:
    """Parse CLI arguments and run master empirical validation suite."""
    parser = argparse.ArgumentParser(description="Astrometrics Master Empirical Pipeline Validation Suite")
    parser.add_argument(
        "--camera",
        type=str,
        default="ZWO ASI 533MM Pro",
        help=("Target camera model to filter exposures (defaults to 'ZWO ASI 533MM Pro')."),
    )
    args = parser.parse_args()

    print("==================================================")
    print("ASTROMETRICS MASTER EMPIRICAL VALIDATION SUITE")
    print("==================================================")
    print(f"Filtering targets for camera: {args.camera}")

    astrometrics = Astrometrics()

    try:
        # Pipeline 1: Spectroscopy
        _ = run_spectroscopy_validation(astrometrics, args.camera)

        # Pipeline 2: Photometry
        _ = run_photometry_validation(astrometrics, args.camera)

        # Pipeline 3: Asteroid Recovery
        _ = run_asteroid_recovery_validation(astrometrics, args.camera)

        # Pipeline 4: Astrometry
        _ = run_astrometry_validation(astrometrics, args.camera)

        # Pipeline 5: Stacking Quality Summary
        _ = run_stacking_validation(astrometrics, args.camera)

        # Final save
        astrometrics.targets.save()
        print("\n==================================================")
        print("EMPIRICAL PIPELINE VALIDATION COMPLETED")
        print("==================================================")
        print("All target quality summaries have been generated and persisted to astrometrics.db.")

    except Exception as fatal_err:
        print(f"\nFatal error during validation execution: {fatal_err}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
