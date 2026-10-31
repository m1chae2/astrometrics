r"""Measure the bias of the odd–even difference by injecting known noise.

The odd–even difference measures a stack's random noise from two half stacks.
It reads a little low (clipping in the stacker, a reference frame shared by
both halves), and a low random part makes fixed-pattern noise look larger.
This script measures the bias for one target: every frame of the target's
library stack is replaced by a simulated frame that keeps the real frame's
stars and calibration but holds Gaussian noise of the frame's predicted size.
The simulated frames go through the same halves, stacker settings and empty
apertures as the real ones, and the script prints the ratio of the noise the
difference implies to the true noise of the whole simulated stack. A ratio
below 1 means the difference reads low; the value is entered as
`ODD_EVEN_DIFFERENCE_NOISE_BIAS` in
`pipelines/stacking/post_processing/difference_noise.py`. How the simulation
works and what the ratio contains are in `pipelines/stacking/odd_even_bias.py`.

Decision D16 of the imaged-target suggestions plan: run it once for each
camera, and again whenever the stacker's rejection settings change. Run it on
a copy of the library (``build/agent_tools/realrun.sh``):

    python -m astrometricslib.scripts.measure_odd_even_bias "M 13" \
        --work-folder /path/to/scratch --repeats 3 --json bias_zwo.json

The script writes no quality rows and does not change the library stack. It
makes three stacks for each repeat in the target's ``_subsets`` folder and
deletes them afterwards. The simulated frames take about as much disk space as
the real frames, in ``--work-folder``, and are deleted afterwards. Nothing
here is a result until it is entered in the validation register.
"""

import argparse
import json
import os
import sys
import tempfile

from astrometricslib import Astrometrics
from astrometricslib.pipelines.stacking.odd_even_bias import BiasMeasurement, measure_odd_even_bias
from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("target", help="The target whose library stack supplies the frames and apertures.")
    parser.add_argument(
        "--work-folder",
        help="Where the simulated frames are written (default: a new folder in the system temp folder).",
    )
    parser.add_argument(
        "--repeats", type=int, default=1, help="How many times to simulate with new noise (default 1)."
    )
    parser.add_argument("--seed", type=int, default=20261026, help="The seed of the random numbers.")
    parser.add_argument("--json", metavar="PATH", help="Also write the result to this JSON file.")
    return parser


def print_result(measurement: BiasMeasurement) -> None:
    """Print the result in plain words.

    Parameters
    ----------
    measurement : `BiasMeasurement`
        The finished measurement.
    """
    print(f"Target {measurement.target_id}, camera {measurement.camera}")
    print(
        f"  {measurement.frame_count} frames; halves of {measurement.odd_half_frames} and "
        f"{measurement.even_half_frames} (the shared reference frame counted in both); "
        f"weighting {measurement.stack_weight}"
    )
    for number, repeat in enumerate(measurement.repeats, start=1):
        print(
            f"  repeat {number}: ratio {repeat.ratio:.4f} +/- {repeat.ratio_error:.4f} "
            f"({repeat.aperture_count} apertures)"
        )
    if measurement.ratio is None:
        print("  no repeat finished")
        return
    print(
        f"  difference noise over true noise: {measurement.ratio:.4f} +/- {measurement.ratio_error:.4f}"
        " (below 1: the difference reads low)"
    )
    for note in measurement.notes:
        print(f"  note: {note}")


def main(arguments: list[str] | None = None) -> int:
    """Run the script.

    Parameters
    ----------
    arguments : `list` [`str`], optional
        The command line. `sys.argv` when left out.

    Returns
    -------
    status : `int`
        0 when the measurement finished.
    """
    options = _build_argument_parser().parse_args(arguments)
    if options.repeats < 1:
        print("--repeats must be at least 1", file=sys.stderr)
        return 2
    astrometrics = Astrometrics()
    target = astrometrics.targets.get(options.target)
    library_stack_path = stack_path_of(target, "imaging")
    work_folder = options.work_folder or tempfile.mkdtemp(prefix="odd_even_bias_")
    measurement = measure_odd_even_bias(
        target,
        library_stack_path,
        configuration=astrometrics.config,
        drivers=None,
        stacking_slot=astrometrics.processing.acquire_stacking_slot,
        work_folder=work_folder,
        repeats=options.repeats,
        seed=options.seed,
        progress=print,
    )
    print_result(measurement)
    if options.json:
        os.makedirs(os.path.dirname(os.path.abspath(options.json)), exist_ok=True)
        with open(options.json, "w", encoding="utf-8") as handle:
            json.dump(measurement.as_dict(), handle, indent=2)
        print(f"Wrote {options.json}")
    return 0


# This guard is required: a Siril run may start worker processes, and Python
# 3.14 on Linux starts them by re-importing this module.
if __name__ == "__main__":
    sys.exit(main())
