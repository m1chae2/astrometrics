"""SA200 spectral registration quality validity check.

Runs Siril's registration step (shift-only transform, matching the
production spectral stacking path in siril_interface.py) against a
target's Star Analyzer 200 ("SPEC") frames, then parses the resulting
.seq registration summary and per-frame .lst star lists to flag
frames where registration looks untrustworthy: too few matched star
pairs, elevated fit RMSE, or the zero-order star's position/amplitude
drifting sharply frame-to-frame.

The registration-quality check alone only registers frames -- it does
not stack -- so it can run standalone alongside other analyses
without contending with a full stacking job for Siril's concurrency
slot. Pass ``--sweep-filters`` to additionally run a small stacking
sweep across filter_wfwhm settings: field-star population is
typically rich enough in spectral frames too (a diffraction grating
disperses every star's light, not just the target's, so most stars
still look like normal point sources), so filter_wfwhm/stack_weight
are no longer gated to standard-imaging-only in siril_interface.py.
The sweep measures the stacked zero-order star's own FWHM
specifically -- not a whole-field median FWHM across many stars,
which is what rejection_threshold_analysis.py uses and which doesn't
capture what matters for a spectral target.
"""

import argparse
import glob
import itertools
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib import Astrometrics
from astrometricslib.image_processing.fits_access import collapse_to_2d

SWEEP_SIGMA = (3.0, 3.0)
SWEEP_FILTER_WFWHM_GRID: list[str | None] = [None, "90%", "80%"]
ZERO_ORDER_FWHM_BOX_RADIUS_PX = 15


def resolve_spec_frames(target, camera: str, date_prefix: str | None = None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Select a target's SPEC (Star Analyzer 200) light frames.

    Excludes derived products (stacked, starless, or starmask
    outputs) so only original captured frames are returned.

    Parameters
    ----------
    target : Target
        The target whose frames should be filtered.
    camera : str
        The exact camera name a frame's camera attribute must match.
    date_prefix : str, optional
        Restricts the selection to frames captured on this date.

    Returns
    -------
    list
        The subset of target.frames with FilterType.SPEC matching
        the given criteria.
    """
    from astrometricslib.utilities.enums import FilterType

    return [
        frame
        for frame in target.frames
        if frame.camera == camera
        and frame.filter == FilterType.SPEC
        and (date_prefix is None or frame.date.startswith(date_prefix))
        and not any(k in frame.path.lower() for k in ("_stacked", "starless", "starmask"))
    ]


def run_siril_script(work_dir: str, commands: list[str], timeout: int = 600) -> str:
    """Run a one-shot Siril console script against a working directory.

    Writes ``commands`` to a ``registration_check.ssf`` script file
    inside ``work_dir`` and invokes the configured Siril executable
    against it.

    Parameters
    ----------
    work_dir : `str`
        Directory to run Siril against (passed as Siril's ``-d``
        argument) and where the generated script file is written.
    commands : `list` of `str`
        Siril console commands to write into the script, in order.
    timeout : `int`, optional
        Maximum time in seconds to wait for Siril to finish, by
        default 600.

    Returns
    -------
    combined_output : `str`
        The subprocess's combined stdout and stderr.
    """
    from astrometricslib.utilities.config_loader import get_configuration

    siril_executable = get_configuration().get_siril_executable()

    script_path = os.path.join(work_dir, "registration_check.ssf")
    with open(script_path, "w") as script_file:
        script_file.write("requires 1.2.0\n")
        for cmd in commands:
            script_file.write(cmd + "\n")

    parts = shlex.split(siril_executable)
    if "flatpak" in parts[0] and "run" in parts:
        idx = parts.index("run")
        parts.insert(idx + 1, "--filesystem=host")
    args = [*parts, "-d", work_dir, "-s", script_path]

    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result.stdout + result.stderr


def measure_zero_order_stacked_fwhm(astrometrics, path: str) -> float | None:  # ruff: ignore[missing-type-function-argument]
    """Measure the FWHM of the brightest detected star in a stacked image.

    For an SA200 stack this is the zero-order star -- the point
    source that matters for spectral extraction -- rather than a
    population median across many field stars (which is what
    rejection_threshold_analysis.py measures for standard imaging).

    Parameters
    ----------
    astrometrics : `Astrometrics`
        the high-level interface providing the `StellarCatalog` used for point-
        source detection.
    path : `str`
        Filesystem path to the stacked FITS image to measure.

    Returns
    -------
    fwhm_px : `float` or `None`
        The full width at half maximum, in pixels, of the brightest
        detected star, or `None` if the image data is empty, no
        source was detected, or the FWHM could not be measured.
    """
    from photutils.morphology import data_properties

    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = collapse_to_2d(np.asarray(data, dtype=float))

    sources = astrometrics.stars.detect_point_sources(data)
    if not sources:
        return None
    brightest = sources[0]
    x = brightest.get("x_centroid", brightest.get("xcentroid"))
    y = brightest.get("y_centroid", brightest.get("ycentroid"))
    if x is None or y is None:
        return None

    box = ZERO_ORDER_FWHM_BOX_RADIUS_PX
    x, y = round(x), round(y)
    y0, y1 = max(0, y - box), min(data.shape[0], y + box)
    x0, x1 = max(0, x - box), min(data.shape[1], x + box)
    cutout = data[y0:y1, x0:x1]
    if cutout.size == 0:
        return None
    try:
        _, median, _ = sigma_clipped_stats(cutout, sigma=3.0)
        fwhm = float(data_properties(cutout - median).fwhm.value)
        return fwhm if np.isfinite(fwhm) and fwhm > 0 else None
    except Exception:
        return None


def run_filter_sweep(astrometrics, target, spec_frames) -> None:  # ruff: ignore[missing-type-function-argument]
    """Sweep filter_wfwhm settings, measuring the zero-order star's FWHM.

    Tests whether field-star-population filtering (previously gated
    to standard-imaging-only on the unverified assumption that
    spectral frames have too few stars) actually sharpens the
    zero-order star, now that filter_wfwhm/stack_weight apply to
    spectral stacks too. Writes the full sweep results to a JSON file
    under the configured logs path.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        the high-level interface providing the `TargetCatalog` used for
        stacking and quality measurement.
    target : `Target`
        The target being stacked; used to resolve the logs output
        path and label the results file.
    spec_frames : `list`
        The SPEC frames to stack for each `SWEEP_FILTER_WFWHM_GRID`
        setting.
    """
    results = []
    for filter_wfwhm in SWEEP_FILTER_WFWHM_GRID:
        label = f"filter_wfwhm={filter_wfwhm or 'off'}"
        print(f"\n=== Stacking with sigma={SWEEP_SIGMA}, {label} ===")

        row = {
            "filter_wfwhm": filter_wfwhm or "off",
            "elapsed_s": None,
            "zero_order_fwhm": None,
            "rejected_fraction": None,
            "error": None,
        }

        start_time = time.time()
        try:
            stacked_path = astrometrics.processing.run_stacking(
                target,
                frames_to_stack=spec_frames,
                rejection_sigma=SWEEP_SIGMA,
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

        row["zero_order_fwhm"] = measure_zero_order_stacked_fwhm(astrometrics, stacked_path)
        row["rejected_fraction"] = astrometrics.processing.diagnostics.measure_stack_rejected_fraction(
            stacked_path
        )
        print(
            f"Result: zero_order_fwhm={row['zero_order_fwhm']}, "
            f"rejected_fraction={row['rejected_fraction']}, elapsed={row['elapsed_s']:.1f}s"
        )
        results.append(row)

    print("\n==========================================")
    print("SPECTRAL FILTER SWEEP SUMMARY (zero-order star)")
    print("==========================================")
    print(f"{'filter_wfwhm':>13} {'zo_fwhm_px':>11} {'rej_frac':>9} {'elapsed_s':>10}")
    for r in results:
        fwhm_str = f"{r['zero_order_fwhm']:.2f}" if r["zero_order_fwhm"] is not None else "n/a"
        rej_str = f"{r['rejected_fraction']:.3f}" if r["rejected_fraction"] is not None else "n/a"
        elapsed_str = f"{r['elapsed_s']:.1f}" if r["elapsed_s"] is not None else "n/a"
        print(f"{r['filter_wfwhm']:>13} {fwhm_str:>11} {rej_str:>9} {elapsed_str:>10}")

    from astrometricslib.utilities.config_loader import get_configuration

    config = get_configuration()
    safe_id = target.id.replace(" ", "_")
    out_path = os.path.join(str(config.get_logs_path()), f"spectral_filter_sweep_{safe_id}.json")
    with open(out_path, "w") as results_file:
        json.dump(results, results_file, indent=2)
    print(f"\nFull results written to: {out_path}")


def run_analysis() -> None:
    """Register a target's SPEC frames and report registration-quality flags.

    Parses CLI arguments, symlinks the target's matching SPEC frames
    into a scratch Siril work directory, runs Siril's shift-only
    registration, then parses the resulting .seq and .lst outputs to
    flag frames with too few matched star pairs, elevated fit RMSE,
    or a zero-order star whose position or amplitude jumps sharply
    frame-to-frame. Writes the full per-frame results to a JSON file
    under the configured logs path, then removes the scratch work
    directory. Optionally runs `run_filter_sweep` afterward if
    ``--sweep-filters`` was passed.

    Raises
    ------
    SystemExit
        Raised with exit code 1 if the target cannot be found, no
        matching SPEC frames are available, or Siril registration
        does not complete successfully.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    parser = argparse.ArgumentParser(description="SA200 Spectral Registration Quality Validity Check")
    parser.add_argument(
        "target_id", type=str, nargs="?", default="M 13", help="The target ID to process (e.g. 'M 13')."
    )
    parser.add_argument(
        "--date", type=str, default=None, help="Restrict to frames captured on this date (e.g. '2026-05-23')."
    )
    parser.add_argument(
        "--camera", type=str, default="ZWO ASI 533MM Pro", help="Camera to match frames against."
    )
    parser.add_argument(
        "--sweep-filters",
        action="store_true",
        help="Also run a filter_wfwhm stacking sweep, measuring the stacked zero-order star's FWHM.",
    )
    args = parser.parse_args()

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()

    print(f"Resolving Target object for: {args.target_id}")
    target = astrometrics.targets.get(args.target_id)
    if not target:
        print(f"Error: Target '{args.target_id}' not found.")
        raise SystemExit(1)

    date_suffix = f" on {args.date}" if args.date else ""
    print(f"Filtering SPEC frames by camera '{args.camera}'{date_suffix}...")
    spec_frames = resolve_spec_frames(target, args.camera, date_prefix=args.date)

    if not spec_frames:
        print("Error: No SPEC frames available matching the criteria.")
        raise SystemExit(1)

    print(f"Matching SPEC frames: {len(spec_frames)}")

    safe_id = target.id.replace(" ", "_")
    work_dir = os.path.join(os.path.expanduser("~"), "Siril", "Work", f"{safe_id}_spectral_registration_qc")
    lights_dir = os.path.join(work_dir, "lights")
    process_dir = os.path.join(work_dir, "process")
    for folder in (lights_dir, process_dir):
        if os.path.exists(folder):
            shutil.rmtree(folder)
        os.makedirs(folder, exist_ok=True)

    for index, frame in enumerate(spec_frames):
        os.symlink(frame.path, os.path.join(lights_dir, f"light_source_{index + 1:05d}.fits"))

    print("\nRunning Siril conversion + registration (shift-only transform)...")
    output = run_siril_script(
        work_dir,
        [
            "cd lights",
            "convert light_source -out=../process -fitseq",
            "cd ../process",
            "register light_source -transf=shift",
        ],
    )
    if "Registration finished" not in output and "Sequence processing succeeded" not in output:
        print("Registration did not complete successfully. Siril output:")
        print(output[-4000:])
        raise SystemExit(1)

    seq_frames = astrometrics.processing.diagnostics.parse_stack_registration_seq(
        os.path.join(process_dir, "light_source.seq")
    )
    lst_paths = sorted(
        glob.glob(os.path.join(process_dir, "cache", "light_source*.lst")),
        key=lambda p: int(os.path.splitext(os.path.basename(p))[0].replace("light_source", "")),
    )
    zero_order_stars = [astrometrics.processing.diagnostics.parse_stack_zero_order_star(p) for p in lst_paths]

    if len(seq_frames) != len(spec_frames) or len(zero_order_stars) != len(spec_frames):
        print(
            f"Warning: parsed {len(seq_frames)} .seq entries and {len(zero_order_stars)} .lst files "
            f"for {len(spec_frames)} input frames -- some frames may have failed registration."
        )

    nb_stars = [f["nb_stars"] for f in seq_frames]
    rmse_values = [f["rmse"] for f in seq_frames]
    peak_to_background_ratios = [s["peak_to_background_ratio"] if s else None for s in zero_order_stars]

    position_jumps: list[float | None] = [None]
    for prev, curr in itertools.pairwise(zero_order_stars):
        if prev is None or curr is None:
            position_jumps.append(None)
        else:
            position_jumps.append(float(np.hypot(curr["x"] - prev["x"], curr["y"] - prev["y"])))

    thresholds = astrometrics.processing.diagnostics.spectral_registration_thresholds
    low_star_count_flags = [n < thresholds["min_matched_star_pairs"] for n in nb_stars]
    high_rmse_flags = astrometrics.processing.diagnostics.flag_value_outliers(
        rmse_values, thresholds["rmse_outlier_sigma"]
    )
    dim_zero_order_flags = astrometrics.processing.diagnostics.flag_value_outliers(
        peak_to_background_ratios, thresholds["zero_order_amplitude_outlier_sigma"], low_is_bad=True
    )
    position_jump_flags = astrometrics.processing.diagnostics.flag_value_outliers(
        position_jumps, thresholds["zero_order_position_jump_outlier_sigma"]
    )

    results = []
    print("\n==========================================")
    print("SPECTRAL REGISTRATION QUALITY SUMMARY")
    print("==========================================")
    print(f"{'frame':>5} {'nb_stars':>9} {'rmse':>10} {'zo_ratio':>10} {'zo_jump':>9}  flags")
    for i, frame in enumerate(spec_frames):
        seq_entry = seq_frames[i] if i < len(seq_frames) else {}
        zero_order = zero_order_stars[i] if i < len(zero_order_stars) else None

        flags = []
        if i < len(low_star_count_flags) and low_star_count_flags[i]:
            flags.append("low_star_count")
        if i < len(high_rmse_flags) and high_rmse_flags[i]:
            flags.append("high_rmse")
        if i < len(dim_zero_order_flags) and dim_zero_order_flags[i]:
            flags.append("dim_zero_order")
        if i < len(position_jump_flags) and position_jump_flags[i]:
            flags.append("zero_order_position_jump")

        row = {
            "path": frame.path,
            "nb_stars": seq_entry.get("nb_stars"),
            "rmse": seq_entry.get("rmse"),
            "roundness": seq_entry.get("roundness"),
            "zero_order_peak_to_background_ratio": (
                zero_order["peak_to_background_ratio"] if zero_order else None
            ),
            "zero_order_fwhm_x_px": zero_order["fwhm_x_px"] if zero_order else None,
            "zero_order_position_jump_px": position_jumps[i] if i < len(position_jumps) else None,
            "flags": flags,
        }
        results.append(row)

        rmse_str = f"{row['rmse']:.5f}" if row["rmse"] is not None else "n/a"
        ratio_str = (
            f"{row['zero_order_peak_to_background_ratio']:.1f}"
            if row["zero_order_peak_to_background_ratio"] is not None
            else "n/a"
        )
        jump_str = (
            f"{row['zero_order_position_jump_px']:.2f}"
            if row["zero_order_position_jump_px"] is not None
            else "n/a"
        )
        print(
            f"{i:>5} {row['nb_stars']!s:>9} {rmse_str:>10} {ratio_str:>10} "
            f"{jump_str:>9}  {','.join(flags) or '-'}"
        )

    flagged_count = sum(1 for r in results if r["flags"])
    print(f"\n{flagged_count} of {len(results)} frames flagged.")

    from astrometricslib.utilities.config_loader import get_configuration

    config = get_configuration()
    date_tag = f"_{args.date}" if args.date else ""
    out_path = os.path.join(
        str(config.get_logs_path()), f"spectral_registration_quality_{safe_id}{date_tag}.json"
    )
    with open(out_path, "w") as results_file:
        json.dump(results, results_file, indent=2)
    print(f"\nFull results written to: {out_path}")

    shutil.rmtree(work_dir, ignore_errors=True)

    if args.sweep_filters:
        run_filter_sweep(astrometrics, target, spec_frames)


if __name__ == "__main__":
    run_analysis()
