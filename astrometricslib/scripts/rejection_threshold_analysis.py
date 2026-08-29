"""Empirical sigma x filter-percentile grid analysis for stack rejection.

Sweeps rejection-sigma and native Siril ``-filter-wfwhm`` percentile
combinations against a target's frames, measuring stacked-image FWHM
and rejection-map fraction for each combination. Results inform the
Chauvenet-based adaptive rejection defaults and the
minimum-surviving-frames floor in the stacking quality-validation
plan.
"""

import argparse
import json
import logging
import os
import sys
import time

from astrometricslib import Astrometrics

SIGMA_GRID: list[tuple[float, float]] = [(2.0, 2.0), (2.5, 2.5), (3.0, 3.0), (3.5, 3.5), (4.0, 4.0)]
FILTER_WFWHM_GRID: list[str | None] = [None, "90%", "80%"]


def resolve_target_frames(  # ruff: ignore[missing-return-type-undocumented-public-function]
    target,  # ruff: ignore[missing-type-function-argument]
    camera: str,
    filter_type_names: tuple[str, ...],
    date_prefix: str | None = None,
):
    """Select a target's light frames matching camera/filter criteria.

    Excludes derived products (stacked, starless, or starmask
    outputs) so only original captured frames are returned.

    Parameters
    ----------
    target : Target
        The target whose frames should be filtered.
    camera : str
        The exact camera name a frame's camera attribute must match.
    filter_type_names : tuple of str
        Names of FilterType enum members a frame's filter must match.
    date_prefix : str, optional
        Restricts the selection to frames captured on this date.
        Matched as a prefix of FrameRecord.date.

    Returns
    -------
    list
        The subset of target.frames matching the criteria.
    """
    from astrometricslib.utilities.enums import FilterType

    matching_filters = tuple(getattr(FilterType, name) for name in filter_type_names)
    return [
        frame
        for frame in target.frames
        if frame.camera == camera
        and frame.filter in matching_filters
        and (date_prefix is None or frame.date.startswith(date_prefix))
        and not any(k in frame.path.lower() for k in ("_stacked", "starless", "starmask"))
    ]


def run_analysis() -> None:
    """Run the sigma x filter-wfwhm-percentile sweep and report results.

    Parses CLI arguments, resolves the requested target's matching
    frames, stacks them once per combination in `SIGMA_GRID` x
    `FILTER_WFWHM_GRID`, measures the resulting FWHM and rejected
    fraction for each combination, prints a summary table, and writes
    the full results to a JSON file under the configured logs path.

    Raises
    ------
    SystemExit
        Raised with exit code 1 if the target has no frames, or no
        frames match the camera/filter/date criteria.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    parser = argparse.ArgumentParser(description="Sigma x Filter-Percentile Rejection Threshold Analysis")
    parser.add_argument(
        "target_id", type=str, nargs="?", default="M 81", help="The target ID to process (e.g. 'M 81')."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Restrict to frames captured on this date (e.g. '2026-05-23').",
    )
    args = parser.parse_args()

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()
    target_id = args.target_id

    print(f"Resolving Target object for: {target_id}")
    target = astrometrics.targets.get(target_id)
    if not target:
        target = astrometrics.targets.create(target_id)

    if not target.frames:
        print("Error: No frames available to stack.")
        raise SystemExit(1)

    camera = "ZWO ASI 533MM Pro"
    date_suffix = f" on {args.date}" if args.date else ""
    print(f"Filtering frames by camera '{camera}' and filter 'L'{date_suffix}...")
    target_frames = resolve_target_frames(target, camera, ("L", "NONE"), date_prefix=args.date)

    if not target_frames:
        print("Error: No frames available to stack matching the criteria.")
        raise SystemExit(1)

    print(f"Matching frames to stack: {len(target_frames)}")

    results = []
    for sigma_low, sigma_high in SIGMA_GRID:
        for filter_wfwhm in FILTER_WFWHM_GRID:
            label = f"sigma=({sigma_low},{sigma_high}) filter_wfwhm={filter_wfwhm or 'off'}"
            print(f"\n=== Stacking with {label} ===")

            row = {
                "sigma_low": sigma_low,
                "sigma_high": sigma_high,
                "filter_wfwhm": filter_wfwhm or "off",
                "elapsed_s": None,
                "stacked_fwhm": None,
                "rejected_fraction": None,
                "error": None,
            }

            start_time = time.time()
            try:
                stacked_path = astrometrics.processing.run_stacking(
                    target,
                    frames_to_stack=target_frames,
                    rejection_sigma=(sigma_low, sigma_high),
                    filter_wfwhm=filter_wfwhm,
                    stack_weight="wfwhm",
                    generate_rejmap=True,
                )
            except Exception as stack_err:
                row["error"] = str(stack_err)
                row["elapsed_s"] = time.time() - start_time
                print(f"Stacking failed for {label}: {stack_err}")
                results.append(row)
                continue

            row["elapsed_s"] = time.time() - start_time

            if not stacked_path or not os.path.exists(stacked_path):
                row["error"] = "no output"
                print(f"Stacking produced no output for {label}.")
                results.append(row)
                continue

            row["stacked_fwhm"] = astrometrics.processing.diagnostics.measure_stack_fwhm(stacked_path)
            row["rejected_fraction"] = astrometrics.processing.diagnostics.measure_stack_rejected_fraction(
                stacked_path
            )
            print(
                f"Result: FWHM={row['stacked_fwhm']}, rejected_fraction={row['rejected_fraction']}, "
                f"elapsed={row['elapsed_s']:.1f}s"
            )
            results.append(row)

    print("\n==========================================")
    print("REJECTION THRESHOLD ANALYSIS SUMMARY")
    print("==========================================")
    print(
        f"{'sigma_low':>9} {'sigma_high':>10} {'filter_wfwhm':>13} "
        f"{'fwhm_px':>9} {'rej_frac':>9} {'elapsed_s':>10}"
    )
    for r in results:
        fwhm_str = f"{r['stacked_fwhm']:.2f}" if r["stacked_fwhm"] is not None else "n/a"
        rej_str = f"{r['rejected_fraction']:.3f}" if r["rejected_fraction"] is not None else "n/a"
        elapsed_str = f"{r['elapsed_s']:.1f}" if r["elapsed_s"] is not None else "n/a"
        print(
            f"{r['sigma_low']:>9} {r['sigma_high']:>10} {r['filter_wfwhm']:>13} "
            f"{fwhm_str:>9} {rej_str:>9} {elapsed_str:>10}"
        )

    from astrometricslib.utilities.config_loader import get_configuration

    config = get_configuration()
    safe_id = target.id.replace(" ", "_")
    date_tag = f"_{args.date}" if args.date else ""
    out_path = os.path.join(
        str(config.get_logs_path()), f"rejection_threshold_analysis_{safe_id}{date_tag}.json"
    )
    with open(out_path, "w") as results_file:
        json.dump(results, results_file, indent=2)
    print(f"\nFull results written to: {out_path}")


if __name__ == "__main__":
    run_analysis()
