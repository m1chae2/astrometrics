r"""Measure what the asteroid detector finds, misses and invents.

The detector chains point sources across frames into straight tracks, then
keeps the tracks that move steadily. Its result is only worth reading if it
is known how often it finds a real mover and how often it invents one. This
script measures both.

1. **Recovery on synthetic fields.** Artificial frames with a fixed set of
   stars, noise, and optionally one injected mover of a chosen brightness and
   speed. For each brightness and speed the share of injected movers the
   detector confirms is counted, and in fields with no mover the number of
   tracks it confirms anyway.

2. **Confirmed movers on real fields.** The detector is run, read-only, on the
   saved frames of targets far from the ecliptic, where almost no real asteroid
   can be in the field, so a confirmed mover that matches no known asteroid is
   almost certainly false (a satellite, a hot pixel or an artefact).

Synthetic frames share one pointing, so they test detection and chaining, not
the error in each real frame's position; the real-field run shows that. Both
results go in the detector's README.

    python -m astrometricslib.scripts.validate_asteroid_detection --synthetic
    python -m astrometricslib.scripts.validate_asteroid_detection \
        --real "NGC 2403" "M 81"
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.pipeline import AsteroidDetectionPipeline

# Synthetic frame geometry: 1.8 arcseconds per pixel (0.0005 degrees), frames
# 300 seconds apart.
FRAME_SIZE_PX = 256
PIXEL_SCALE_ARCSEC = 1.8
SECONDS_BETWEEN_FRAMES = 300.0
DEFAULT_FRAME_COUNT = 8
NOISE_SIGMA = 5.0
SKY_LEVEL = 100.0
STAR_COUNT = 25
STAR_PEAK = 3000.0
STAR_SIGMA_PX = 2.0

# A recovered mover must follow the injected track to within this many pixels
# in at least this share of the frames it was injected into.
MATCH_TOLERANCE_PX = 3.0

CONFIRMED_STAGES = (CascadeStage.RATE_LINEARITY_CONFIRMED, CascadeStage.EPHEMERIS_MATCHED)


@dataclass(frozen=True)
class Mover:
    """An injected moving source.

    Attributes
    ----------
    start_xy : `tuple` [`float`, `float`]
        Pixel position in the first frame.
    velocity_xy : `tuple` [`float`, `float`]
        Pixels moved per frame.
    peak_snr : `float`
        Peak brightness above the sky, in units of the noise.
    """

    start_xy: tuple[float, float]
    velocity_xy: tuple[float, float]
    peak_snr: float

    @property
    def rate_arcsec_per_hour(self) -> float:
        """Give the mover's speed on the sky.

        Returns
        -------
        rate : `float`
            Arcseconds per hour.
        """
        pixels_per_frame = float(np.hypot(*self.velocity_xy))
        return pixels_per_frame * PIXEL_SCALE_ARCSEC * 3600.0 / SECONDS_BETWEEN_FRAMES

    def position(self, frame_index: int) -> tuple[float, float]:
        """Give where the mover is in a frame.

        Parameters
        ----------
        frame_index : `int`
            The frame number, from 0.

        Returns
        -------
        position : `tuple` [`float`, `float`]
            Pixel position.
        """
        return (
            self.start_xy[0] + self.velocity_xy[0] * frame_index,
            self.start_xy[1] + self.velocity_xy[1] * frame_index,
        )


def make_mover(peak_snr: float, rate_arcsec_per_hour: float, generator: np.random.Generator) -> Mover:
    """Build a mover with a random start and direction at a given speed.

    Parameters
    ----------
    peak_snr : `float`
        Peak brightness in units of the noise.
    rate_arcsec_per_hour : `float`
        The speed on the sky.
    generator : `numpy.random.Generator`
        The source of randomness.

    Returns
    -------
    mover : `Mover`
        A mover that stays on the frame for the whole sequence.
    """
    pixels_per_frame = rate_arcsec_per_hour * SECONDS_BETWEEN_FRAMES / 3600.0 / PIXEL_SCALE_ARCSEC
    angle = generator.uniform(0.0, 2.0 * np.pi)
    velocity = (pixels_per_frame * np.cos(angle), pixels_per_frame * np.sin(angle))
    reach = pixels_per_frame * (DEFAULT_FRAME_COUNT - 1)
    margin = 12.0 + reach
    centre = (
        generator.uniform(margin, FRAME_SIZE_PX - margin, size=2)
        if margin < FRAME_SIZE_PX / 2
        else (128, 128)
    )
    start = (
        float(centre[0] - velocity[0] * (DEFAULT_FRAME_COUNT - 1) / 2),
        float(centre[1] - velocity[1] * (DEFAULT_FRAME_COUNT - 1) / 2),
    )
    return Mover(start, (float(velocity[0]), float(velocity[1])), float(peak_snr))


def write_synthetic_field(
    directory: Path, mover: Mover | None, seed: int, frame_count: int = DEFAULT_FRAME_COUNT
) -> tuple[list[tuple[str, float]], str]:
    """Write a stack and a sequence of synthetic frames.

    Parameters
    ----------
    directory : `pathlib.Path`
        Where to write the files.
    mover : `Mover` or `None`
        The mover to inject, or `None` for a field with none.
    seed : `int`
        Seed for the stars and the noise.
    frame_count : `int`, optional
        How many frames.

    Returns
    -------
    frames, stack_path : `tuple`
        Each frame's path and timestamp, and the path of the stack.
    """
    generator = np.random.default_rng(seed)
    star_positions = generator.uniform(10, FRAME_SIZE_PX - 10, size=(STAR_COUNT, 2))
    yy, xx = np.mgrid[0:FRAME_SIZE_PX, 0:FRAME_SIZE_PX]
    star_image = np.zeros((FRAME_SIZE_PX, FRAME_SIZE_PX), dtype=np.float32)
    for x, y in star_positions:
        star_image += (STAR_PEAK * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * STAR_SIGMA_PX**2))).astype(
            np.float32
        )
    frames = []
    for index in range(frame_count):
        data = (
            generator.normal(SKY_LEVEL, NOISE_SIGMA, (FRAME_SIZE_PX, FRAME_SIZE_PX)).astype(np.float32)
            + star_image
        )
        if mover is not None:
            x, y = mover.position(index)
            data += (
                mover.peak_snr
                * NOISE_SIGMA
                * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * STAR_SIGMA_PX**2))
            ).astype(np.float32)
        header = fits.Header()
        header["RA"] = 150.0
        header["DEC"] = 0.0
        path = directory / f"frame{index}.fits"
        fits.PrimaryHDU(data, header=header).writeto(path, overwrite=True)
        frames.append((str(path), index * SECONDS_BETWEEN_FRAMES))
    stack_header = fits.Header()
    for key, value in {
        "NAXIS1": FRAME_SIZE_PX,
        "NAXIS2": FRAME_SIZE_PX,
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": 150.0,
        "CRVAL2": 0.0,
        "CRPIX1": FRAME_SIZE_PX / 2.0,
        "CRPIX2": FRAME_SIZE_PX / 2.0,
        "CD1_1": -PIXEL_SCALE_ARCSEC / 3600.0,
        "CD1_2": 0.0,
        "CD2_1": 0.0,
        "CD2_2": PIXEL_SCALE_ARCSEC / 3600.0,
        "CUNIT1": "deg",
        "CUNIT2": "deg",
    }.items():
        stack_header[key] = value
    stack_path = directory / "stack.fits"
    fits.PrimaryHDU(star_image + SKY_LEVEL, header=stack_header).writeto(stack_path, overwrite=True)
    return frames, str(stack_path)


def candidate_follows_mover(candidate: AsteroidDetectionCandidate, mover: Mover, frame_count: int) -> bool:
    """Say whether a candidate is the injected mover.

    Parameters
    ----------
    candidate : `AsteroidDetectionCandidate`
        A track the detector found.
    mover : `Mover`
        The injected mover.
    frame_count : `int`
        The number of frames.

    Returns
    -------
    follows : `bool`
        True if the candidate's detections lie within `MATCH_TOLERANCE_PX` of
        the mover's true positions in at least three frames.
    """
    timestamps = [index * SECONDS_BETWEEN_FRAMES for index in range(frame_count)]
    close = 0
    for detection in candidate.frame_detections:
        if detection.timestamp not in timestamps:
            continue
        true_x, true_y = mover.position(timestamps.index(detection.timestamp))
        if np.hypot(detection.pixel_x - true_x, detection.pixel_y - true_y) <= MATCH_TOLERANCE_PX:
            close += 1
    return close >= 3


@dataclass(frozen=True)
class Trial:
    """What one synthetic run produced.

    Attributes
    ----------
    confirmed : `int`
        Tracks the detector confirmed.
    recovered : `bool`
        Whether one of them is the injected mover (False when none injected).
    """

    confirmed: int
    recovered: bool


def run_trial(mover: Mover | None, seed: int, config: MovingObjectConfig | None = None) -> Trial:
    """Run the detector on one synthetic field.

    Parameters
    ----------
    mover : `Mover` or `None`
        The mover to inject.
    seed : `int`
        Seed for the field.
    config : `MovingObjectConfig`, optional
        Detector settings; the defaults when omitted.

    Returns
    -------
    trial : `Trial`
        The number of tracks confirmed and whether the mover was among them.
    """
    with tempfile.TemporaryDirectory() as directory:
        frames, stack = write_synthetic_field(Path(directory), mover, seed)
        pipeline = AsteroidDetectionPipeline(config or MovingObjectConfig())
        candidates = pipeline.process("synthetic", stack, frames)
    confirmed = [candidate for candidate in candidates if candidate.cascade_stage in CONFIRMED_STAGES]
    recovered = mover is not None and any(
        candidate_follows_mover(candidate, mover, DEFAULT_FRAME_COUNT) for candidate in confirmed
    )
    return Trial(len(confirmed), recovered)


def recovery_grid(
    peak_snrs: Sequence[float], rates: Sequence[float], trials: int, seed: int = 0
) -> dict[tuple[float, float], float]:
    """Measure the recovered share for each brightness and speed.

    Parameters
    ----------
    peak_snrs : `Sequence` [`float`]
        Peak brightnesses, in units of the noise.
    rates : `Sequence` [`float`]
        Speeds in arcseconds per hour.
    trials : `int`
        Fields per cell.
    seed : `int`, optional
        Base seed.

    Returns
    -------
    recovery : `dict` [`tuple` [`float`, `float`], `float`]
        For each (peak SNR, rate), the share of injected movers recovered.
    """
    generator = np.random.default_rng(seed)
    result = {}
    for snr in peak_snrs:
        for rate in rates:
            hits = sum(
                run_trial(make_mover(snr, rate, generator), int(generator.integers(1 << 30))).recovered
                for _ in range(trials)
            )
            result[snr, rate] = hits / trials
    return result


def false_track_counts(trials: int, seed: int = 1) -> list[int]:
    """Count confirmed tracks in fields that have no mover.

    Parameters
    ----------
    trials : `int`
        How many fields.
    seed : `int`, optional
        Base seed.

    Returns
    -------
    counts : `list` [`int`]
        The number of confirmed tracks in each field; every one is false.
    """
    generator = np.random.default_rng(seed)
    return [run_trial(None, int(generator.integers(1 << 30))).confirmed for _ in range(trials)]


def format_recovery(
    recovery: dict[tuple[float, float], float], peak_snrs: Sequence[float], rates: Sequence[float]
) -> str:
    """Write the recovery grid as a table.

    Returns
    -------
    text : `str`
        Rows are peak brightness, columns are speeds.
    """
    header = "peak SNR   " + "".join(f'{rate:>10.0f}"/h' for rate in rates)
    lines = [header]
    for snr in peak_snrs:
        lines.append(f"{snr:8.0f}   " + "".join(f"{recovery[snr, rate]:>12.0%}" for rate in rates))
    return "\n".join(lines)


def real_field_counts(database: str, target_id: str, maximum_frames: int | None = None) -> Counter:
    """Run the detector, read-only, on a target's saved frames.

    Parameters
    ----------
    database : `str`
        Path to the catalog database.
    target_id : `str`
        The target.
    maximum_frames : `int`, optional
        Use only the first frames; all of them when omitted.

    Returns
    -------
    counts : `collections.Counter`
        Candidates at each cascade stage, plus ``frames`` used.

    Raises
    ------
    ValueError
        If the database has no target of that name.
    """
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT data_json FROM targets WHERE id = ?", (target_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"No target named {target_id!r}.")
    document = json.loads(row[0])
    frames = [
        (frame["path"], float(frame["timestamp"]))
        for frame in document.get("frames", [])
        if str(frame.get("role", "")).upper() == "LIGHT"
        and frame.get("timestamp") is not None
        and os.path.exists(frame.get("path", ""))
    ]
    frames.sort(key=lambda item: item[1])
    if maximum_frames:
        frames = frames[:maximum_frames]
    stack = (document.get("stacking") or {}).get("stackedImage") or ""
    pipeline = AsteroidDetectionPipeline()
    candidates = pipeline.process(target_id, stack, frames)
    counts = Counter(candidate.cascade_stage.name for candidate in candidates)
    counts["frames"] = len(frames)
    counts["ephemeris_matched"] = pipeline.last_run_metrics.get("candidates_ephemeris_matched", 0)
    counts["rate_linearity_confirmed"] = pipeline.last_run_metrics.get(
        "candidates_rate_linearity_confirmed", 0
    )
    return counts


def main(argv: list[str] | None = None) -> int:
    """Run the validation from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if a real-field run was asked for and the
        catalog database cannot be found.
    """
    parser = argparse.ArgumentParser(prog="validate_asteroid_detection", description=__doc__.split("\n")[0])
    parser.add_argument("--synthetic", action="store_true", help="Run the injection-recovery grid.")
    parser.add_argument("--trials", type=int, default=12, help="Fields per cell of the grid.")
    parser.add_argument(
        "--null-trials", type=int, default=40, help="Mover-free fields to count false tracks in."
    )
    parser.add_argument(
        "--real", nargs="*", default=None, metavar="TARGET", help="Targets to run on their real frames."
    )
    parser.add_argument(
        "--maximum-frames", type=int, default=None, help="Use only the first frames of each real target."
    )
    arguments = parser.parse_args(argv)

    if arguments.synthetic:
        snrs = (3.0, 5.0, 8.0, 12.0, 20.0)
        rates = (5.0, 20.0, 60.0, 150.0)
        print("Share of injected movers recovered (synthetic fields):")
        print(format_recovery(recovery_grid(snrs, rates, arguments.trials), snrs, rates))
        counts = false_track_counts(arguments.null_trials)
        print(
            f"\nConfirmed tracks in {len(counts)} fields with no mover: total {sum(counts)}, "
            f"fields with at least one {sum(1 for count in counts if count)}"
        )
    if arguments.real is not None:
        from astrometricslib import Astrometrics

        database = os.path.join(str(Astrometrics().config.get_library_path()), "astrometrics.db")
        if not os.path.exists(database):
            print(f"No catalog database at {database}.")
            return 1
        for target_id in arguments.real:
            counts = real_field_counts(database, target_id, arguments.maximum_frames)
            print(f"\n{target_id}: {dict(counts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
