"""Work out the tuned values of both libraries from this observatory's data.

Each tuned value (a limit, a threshold, a typical width) is derived from the
setup's own stored frames, stacks, spectra and guiding records by a tuner.
This recipe runs every tuner for one setup and prints what would be set or
moved. It saves nothing until you set ``SAVE = True``; then the config is
backed up first and each value is written with where it came from.

Edit the settings below, then run it:

- ``SETUP``: the setup to tune, or ``None`` for the active one.
- ``IMAGE_TUNERS`` and ``OBSERVATORY_TUNERS``: tuner names to run, or an
  empty list for all of that library's tuners. The report names each tuner.
- ``SAVE``: leave ``False`` to preview.
"""

from astrometricslib import Astrometrics, get_configuration
from wayfindinglib import Wayfinder

SETUP: str | None = None
IMAGE_TUNERS: list[str] = []
OBSERVATORY_TUNERS: list[str] = []
SAVE = False


def print_report(title: str, report: object) -> None:
    """Print one library's tuning report.

    Parameters
    ----------
    title : `str`
        The library the report is for.
    report : `TuningReport`
        What the tuners found.
    """
    print(f"{title}, setup {report.setup_name}:")
    for notice in report.notices:
        print(f"  {notice.sentence()}")
    for line in report.rejected:
        print(f"  refused: {line}")
    for line in report.skipped:
        print(f"  skipped: {line}")
    print(f"  {len(report.notices)} to set or move, {len(report.unchanged)} unchanged.")
    if report.written:
        print(f"  Saved; the config was backed up to {report.backup_path}.")


def run_tuning_recipe() -> None:
    """Run the tuners of both libraries for the chosen setup and print them."""
    configuration = get_configuration()
    astrometrics = Astrometrics(configuration)
    wayfinder = Wayfinder(configuration, astrometrics)
    report = astrometrics.quality.measure_tuned_parameters(SETUP, IMAGE_TUNERS or None, save=SAVE)
    print_report("Image processing", report)
    report = wayfinder.review.measure_tuned_parameters(SETUP, OBSERVATORY_TUNERS or None, save=SAVE)
    print_report("Observatory", report)
    if not SAVE:
        print("Nothing written; set SAVE = True to save the values.")


if __name__ == "__main__":
    run_tuning_recipe()
