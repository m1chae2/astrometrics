"""Purpose: Check the centering exposure calculator against real frames.

Description: For each real ZWO frame the script takes the frame's position and
time and asks the calculator (the model behind
`wayfinder.control.mount.get_centering_exposure`) for three things:

1. The star count it predicts at a series of exposures, next to the count a
   5-sigma star finder sees in the frame made into that exposure by
   `solver_needs.thin` (the real frame, thinned to the shorter exposure).
2. The shortest exposure on a fine ladder where it expects its target count.
   The frame is made at that exposure three times and solved with Siril from
   the header position. This tests the margin of the target itself.
3. The exposure it chooses. The frame is made at that exposure and solved with
   Siril, as centering would.

One JSON line per check goes to <study>/out/calculator_check.jsonl. Run on
copies only, through ``build/agent_tools/realrun.sh``, with ``solver_needs.py``
beside it. Usage::

    validate_calculator.py --study <folder> [--only <label> ...]
"""

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import solver_needs as needs
from astropy.io import fits

GAIA_LIMIT = 16.0
"""The Gaia tile cache holds stars to this magnitude. The limits at the short
exposures checked here lie below it, so the counts are complete."""

PREDICT_RATIOS = [1.0, 0.25, 0.0625, 0.03125, 0.015625, 0.0078]


def use_gaia_tile_cache(study: Path) -> None:
    """Make the calculator read the Gaia tile cache copy.

    The deep star catalog is not installed on the copy. The tile cache copy
    holds Gaia stars to G = 16 around every imaged field, which is deeper than
    the limits of the short exposures checked here.

    Parameters
    ----------
    study : `pathlib.Path`
        The study folder, whose ``work/catalog_cache_copy.db`` is read.
    """
    from astrometricslib._internal.drivers import catalog_store
    from astrometricslib._internal.drivers.sky_tiles import tiles_for_cone
    from astrometricslib.foundation.parameters import resolve
    from wayfindinglib.drivers.catalog import deep_star_store

    catalog_store.get_catalog_cache_path = lambda config: study / "work" / "catalog_cache_copy.db"

    def find(
        config: object,
        ra: float,
        dec: float,
        radius: float,
        magnitude_limit: float,
        maximum_stars: int | None = None,
    ) -> list[tuple[int, float, float, float]]:
        """Give the cached stars in a circle, to the cache's depth.

        Returns
        -------
        stars : `list` [`tuple`]
            ``(source id, ra, dec, G)`` of each star in the circle.
        """
        level = int(resolve(catalog_store.GAIA_TILE_LEVEL).value)
        tiles = tiles_for_cone(ra, dec, radius, level)
        rows = catalog_store.stars_in_tiles(None, tiles, min(magnitude_limit, GAIA_LIMIT))
        cos_dec = math.cos(math.radians(dec))
        return [
            (int(row[0]), row[1], row[2], row[3])
            for row in rows
            if math.hypot((row[1] - ra) * cos_dec, row[2] - dec) <= radius
        ]

    deep_star_store.find_deep_stars = find
    deep_star_store.read_catalog_status = lambda config: {
        "installed": True,
        "complete": True,
        "magnitude_limit": GAIA_LIMIT,
    }


def join_sweep(study: Path, context: object, needs_module: object) -> None:
    """Add the calculator's prediction to each exposure copy of the sweep.

    Parameters
    ----------
    study : `pathlib.Path`
        The study folder; ``out/solver_needs.jsonl`` is read.
    context : `object`
        The control context of the calculator.
    needs_module : `object`
        The ``solver_needs`` module, for the frame table.
    """
    from astrometricslib import SkyPosition
    from wayfindinglib.tasks.control_tasks.centering_exposure import (
        predict_star_counts,
    )

    lines = [json.loads(line) for line in (study / "out" / "solver_needs.jsonl").read_text().splitlines()]
    out = open(study / "out" / "sweep_with_predictions.jsonl", "w")
    for label, relative in needs_module.FRAMES.items():
        if not label.startswith("zwo"):
            continue
        header = fits.getheader(needs_module.LIBRARY / relative)
        nx, ny = header["NAXIS1"], header["NAXIS2"]
        position = SkyPosition(ra_deg=float(header["RA"]), dec_deg=float(header["DEC"]))
        moment = datetime.fromisoformat(str(header["DATE-OBS"])).replace(tzinfo=UTC).timestamp()
        factor = nx * ny / (math.pi * (min(nx, ny) / 2.0) ** 2)
        rows = [r for r in lines if r["frame"] == label and r["kind"] == "exposure"]
        trials, _, missing = predict_star_counts(
            context, position, [r["exposure_s"] for r in rows], epoch_seconds=moment
        )
        if missing:
            print(label, missing, flush=True)
            continue
        for row, trial in zip(rows, trials, strict=True):
            row["predicted_in_circle"] = trial.expected_star_count
            row["predicted_in_frame"] = round(trial.expected_star_count * factor, 1)
            row["limiting_magnitude"] = round(trial.limiting_magnitude, 2)
            out.write(json.dumps(row) + "\n")
            print(row, flush=True)
    out.close()


def main() -> None:
    """Run the checks."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--study", required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument(
        "--join-sweep",
        action="store_true",
        help="Only add the calculator's prediction to each exposure copy "
        "of solver_needs.jsonl and write sweep_with_predictions.jsonl.",
    )
    arguments = parser.parse_args()
    study = Path(arguments.study).resolve()
    (study / "out").mkdir(parents=True, exist_ok=True)
    (study / "frames").mkdir(exist_ok=True)
    from astrometricslib import SkyPosition
    from astrometricslib._internal.drivers.siril.astrometry.plate_solve import solve_image
    from astrometricslib.parameters import resolve_parameter
    from wayfindinglib import Wayfinder
    from wayfindinglib.drivers.catalog import deep_star_store
    from wayfindinglib.tasks.control_tasks.centering_exposure import (
        TARGET_STAR_COUNT,
        choose_centering_exposure,
        predict_star_counts,
    )

    use_gaia_tile_cache(study)
    context = Wayfinder().control._context
    if arguments.join_sweep:
        join_sweep(study, context, needs)
        return
    rng = np.random.default_rng(1182)
    log = open(study / "out" / "calculator_check.jsonl", "a")
    for label, relative in needs.FRAMES.items():
        if not label.startswith("zwo") or (arguments.only and label not in arguments.only):
            continue
        with fits.open(needs.LIBRARY / relative, memmap=False) as hdus:
            pixels = np.asarray(hdus[0].data, dtype=np.float64)
            header = hdus[0].header.copy()
        ny, nx = pixels.shape
        exposure = float(header["EXPTIME"])
        gain, read_noise = needs.sensor_numbers(str(header.get("INSTRUME", "")))
        position = SkyPosition(ra_deg=float(header["RA"]), dec_deg=float(header["DEC"]))
        moment = datetime.fromisoformat(str(header["DATE-OBS"])).replace(tzinfo=UTC).timestamp()
        circle_to_frame = nx * ny / (math.pi * (min(nx, ny) / 2.0) ** 2)
        work = study / "frames" / f"calc_{label}.fits"
        reference_path = study / "frames" / f"calc_{label}_ref.fits"
        needs.write_frame(reference_path, pixels.astype(np.float32), header, exposure)
        reference = solve_image(str(reference_path), needs.SIRIL, {})
        reference_path.unlink(missing_ok=True)
        exposures = [exposure * ratio for ratio in PREDICT_RATIOS]
        trials, _, missing = predict_star_counts(context, position, exposures, epoch_seconds=moment)
        if missing:
            print(label, "calculator input missing:", missing, flush=True)
            log.write(json.dumps({"frame": label, "missing": missing}) + "\n")
            continue
        for trial, ratio in zip(trials, PREDICT_RATIOS, strict=True):
            data = needs.thin(pixels, ratio, gain, read_noise, rng)
            table, _, _ = needs.detect(data.astype(np.float64))
            detected = 0 if table is None else len(table)
            row = {
                "frame": label,
                "check": "count",
                "exposure_s": trial.exposure_s,
                "limiting_magnitude": round(trial.limiting_magnitude, 2),
                "predicted_in_circle": trial.expected_star_count,
                "predicted_in_frame": round(trial.expected_star_count * circle_to_frame, 1),
                "detected": detected,
            }
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(row, flush=True)
        # The shortest exposure (on a fine ladder) at which the calculator
        # expects the target count, made three times from the real frame,
        # then solved. This tests the margin of the target itself, which the
        # ladder's 1 s first step hides on a dark sky.

        target = resolve_parameter(TARGET_STAR_COUNT).value
        fine = [0.02 * 1.15**step for step in range(40)]
        fine_trials, _, _ = predict_star_counts(context, position, fine, epoch_seconds=moment)
        reached = next((t for t in fine_trials if t.expected_star_count >= target), None)
        if reached is not None and reference is not None:
            for repeat in range(3):
                data = needs.thin(pixels, reached.exposure_s / exposure, gain, read_noise, rng)
                table, _, _ = needs.detect(data.astype(np.float64))
                needs.write_frame(work, data, header, reached.exposure_s)
                solved = solve_image(str(work), needs.SIRIL, {})
                row = {
                    "frame": label,
                    "check": "at_target",
                    "exposure_s": round(reached.exposure_s, 3),
                    "target": target,
                    "predicted_in_circle": reached.expected_star_count,
                    "predicted_in_frame": round(reached.expected_star_count * circle_to_frame, 1),
                    "detected": 0 if table is None else len(table),
                    "repeat": repeat,
                    **needs.judge(reference, solved, nx, ny),
                }
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(row, flush=True)
            work.unlink(missing_ok=True)
        choice = choose_centering_exposure(
            context,
            position,
            epoch_seconds=moment,
            find_stars=deep_star_store.find_deep_stars,
            catalog_status=deep_star_store.read_catalog_status,
        )
        data = needs.thin(pixels, min(choice.exposure_s / exposure, 1.0), gain, read_noise, rng)
        table, _, _ = needs.detect(data.astype(np.float64))
        needs.write_frame(work, data, header, choice.exposure_s)
        solved = solve_image(str(work), needs.SIRIL, {}) if reference is not None else None
        row = {
            "frame": label,
            "check": "choice",
            "chosen_s": choice.exposure_s,
            "source": choice.source,
            "expected_stars": choice.expected_star_count,
            "target": choice.target_star_count,
            "reaches_target": choice.reaches_target,
            "detected": 0 if table is None else len(table),
            **(needs.judge(reference, solved, nx, ny) if reference is not None else {"solved": None}),
        }
        work.unlink(missing_ok=True)
        log.write(json.dumps(row) + "\n")
        log.flush()
        print(row, flush=True)


if __name__ == "__main__":
    main()
