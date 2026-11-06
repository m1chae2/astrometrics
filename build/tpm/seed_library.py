"""Purpose: Fill a throwaway library with fixed, fake content for the TPM run.

Description: The technical performance measures (TPMs) need every display of
the app to show real content: targets with light frames, an imaging stack and
a preview picture, a star with a spectrum and a light curve, calibration
frames, and one observing night with guiding samples. This script writes all
of that into a throwaway folder. It uses a fixed random seed, so two runs
make the same library, and it takes well under a minute.

Safety: the script reads the settings file named by ``ASTROMETRICS_CONFIG``
and refuses to write anything unless the image library, the frames folder and
the wayfinding folder all resolve to places inside the throwaway folder given
on the command line. It never touches hardware.

It seeds through the library's private writers on purpose: it must write
rows no public call can create (fixed times, made-up guiding samples). That
is allowed because ``build/`` is outside the import-linter contracts; the
``banned-api`` exemption for this file in ``pyproject.toml`` says the same.

Set ``TPM_SCALE=N`` (``run_tpm.sh --scale N``) for a bigger library: N times
as many targets (the three real ones plus made-up ones) and N times as many
light frames per target, for a controlled scaling curve.

Usage::

    ASTROMETRICS_CONFIG=<sandbox>/astrometrics.config.toml \
    XDG_DATA_HOME=<sandbox>/xdg_data XDG_CACHE_HOME=<sandbox>/xdg_cache \
    XDG_STATE_HOME=<sandbox>/xdg_state \
    .venv/bin/python build/tpm/seed_library.py <sandbox>
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("ASTROMETRICS_TESTING", "1")

import numpy as np
from astropy.io import fits
from PIL import Image

#: The one fixed random seed. Every run makes the same library.
SEED = 20261106
#: Width and height of the fake frames, in pixels. Small keeps the run fast.
FRAME_SIZE = 256
#: The observing night that gets guiding samples (the date it began).
NIGHT_DATE = "2026-10-12"
#: Light frames per target.
FRAMES_PER_TARGET = 6

#: Targets: id, RA, Dec, camera, telescope.
TARGETS = (
    ("M 31", "00:42:44", "+41:16:09"),
    ("M 13", "16:41:41", "+36:27:35"),
    ("M 81", "09:55:33", "+69:03:55"),
)
CAMERA = "ZWO ASI 533MM Pro"
TELESCOPE = "Apertura 75Q"


def require_inside(path: Path, sandbox: Path, label: str) -> None:
    """Refuse to continue if a configured path is outside the throwaway folder.

    Parameters
    ----------
    path : `pathlib.Path`
        A folder the settings name.
    sandbox : `pathlib.Path`
        The throwaway folder.
    label : `str`
        What the folder is, for the error message.

    Raises
    ------
    RuntimeError
        If `path` is not inside `sandbox`, or is inside the user's real
        library or state folders.
    """
    resolved = path.resolve()
    real_folders = [Path.home() / "Astrometrics", Path.home() / ".local" / "state" / "astrometrics"]
    for real in real_folders:
        if resolved == real.resolve() or real.resolve() in resolved.parents:
            raise RuntimeError(f"Refusing to seed: {label} '{resolved}' is inside the real folder '{real}'.")
    if sandbox.resolve() != resolved and sandbox.resolve() not in resolved.parents:
        raise RuntimeError(
            f"Refusing to seed: {label} '{resolved}' is outside the throwaway folder '{sandbox}'."
        )


def make_image(rng: np.random.Generator, star_count: int = 40, exposure_scale: float = 1.0) -> np.ndarray:
    """Make a fake star field: sky background, noise and Gaussian stars.

    Parameters
    ----------
    rng : `numpy.random.Generator`
        The seeded random source.
    star_count : `int`, optional
        How many stars to draw.
    exposure_scale : `float`, optional
        Multiplies the star brightness.

    Returns
    -------
    image : `numpy.ndarray`
        A float32 image, `FRAME_SIZE` pixels square.
    """
    size = FRAME_SIZE
    y_grid, x_grid = np.mgrid[0:size, 0:size]
    image = rng.normal(500.0, 12.0, (size, size))
    for _ in range(star_count):
        x_center, y_center = rng.uniform(8, size - 8, 2)
        peak = rng.uniform(300, 9000) * exposure_scale
        image += peak * np.exp(-((x_grid - x_center) ** 2 + (y_grid - y_center) ** 2) / (2 * 1.8**2))
    return image.astype(np.float32)


def write_fits(path: Path, image: np.ndarray, **header: object) -> None:
    """Write one FITS file with the given header cards.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it. Parent folders are made.
    image : `numpy.ndarray`
        The pixels.
    **header : `object`
        Header card names and values.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(image)
    for key, value in header.items():
        hdu.header[key.replace("_", "-") if key == "DATE_OBS" else key] = value
    hdu.writeto(path, overwrite=True)


def seed(sandbox: Path) -> dict[str, int]:
    """Fill the library inside `sandbox` and return counts of what was made.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder holding the settings file.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        How many targets, frames, calibration frames and guiding samples
        were written.
    """
    from astrometricslib import Astrometrics, FrameRecord, Target
    from astrometricslib.foundation.config import AppConfiguration
    from wayfindinglib.drivers.control_record_store import ControlRecordStore

    config = AppConfiguration()
    require_inside(config.get_library_path(), sandbox, "image library")
    require_inside(config.get_frames_path(), sandbox, "frames folder")
    require_inside(config.get_stacks_path(), sandbox, "stacks folder")
    wayfinding_dir = sandbox / "wayfinding"
    require_inside(wayfinding_dir, sandbox, "wayfinding library")

    rng = np.random.default_rng(SEED)
    frames_root = config.get_frames_path()
    stacks_root = config.get_stacks_path()
    astrometrics = Astrometrics()
    night_start = datetime.fromisoformat(f"{NIGHT_DATE}T21:00:00").astimezone()
    frame_total = 0

    scale = max(1, int(os.environ.get("TPM_SCALE", "1")))
    extra_targets = tuple(
        (
            f"Synthetic {copy}-{number}",
            f"{(number * 37 + copy) % 24:02d}:10:00",
            f"{(number * 13) % 80 - 40:+03d}:00:00",
        )
        for copy in range(1, scale)
        for number in range(1, len(TARGETS) + 1)
    )
    for target_id, right_ascension, declination in TARGETS + extra_targets:
        records = []
        for index in range(FRAMES_PER_TARGET * scale):
            when = night_start + timedelta(minutes=index)
            path = (
                frames_root
                / "lights"
                / target_id
                / TELESCOPE
                / CAMERA
                / f"{target_id.replace(' ', '_')}_Light_Luminance_{index + 1:03d}.fits"
            )
            write_fits(
                path,
                make_image(rng),
                OBJECT=target_id,
                FILTER="L",
                INSTRUME=CAMERA,
                TELESCOP=TELESCOPE,
                EXPTIME=30.0,
                EXPOSURE=30.0,
                GAIN=100,
                OFFSET=50,
                **{"DATE-OBS": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")},
            )
            records.append(
                FrameRecord(
                    path=str(path),
                    filter="Luminance",
                    role="LIGHT",
                    camera=CAMERA,
                    telescope=TELESCOPE,
                    date=when.strftime("%Y-%m-%d %H:%M:%S"),
                    timestamp=when.timestamp(),
                    exposure="30.0",
                )
            )
            frame_total += 1
        stack_dir = stacks_root / "lights" / target_id
        stack_path = stack_dir / f"{target_id.replace(' ', '_')}_Stacked.fits"
        write_fits(stack_path, make_image(rng, star_count=90, exposure_scale=3.0), OBJECT=target_id)
        preview_path = stack_dir / f"{target_id.replace(' ', '_')}_preview.jpg"
        preview = np.clip(make_image(rng, 90, 3.0) / 12.0, 0, 255).astype(np.uint8)
        Image.fromarray(preview).convert("RGB").save(preview_path, quality=85)
        target = Target(id=target_id, ra=right_ascension, dec=declination, frames=records)
        target.stacking.stacked_image = str(stack_path)
        target.stacking.processed_image = str(preview_path)
        astrometrics.targets.add(target)
    astrometrics.targets.save()

    calibration_total = 0
    for kind, exposure in (("darks", 30.0), ("flats", 0.5), ("bias", 0.0)):
        for index in range(3):
            path = frames_root / kind / CAMERA / f"{kind}_{index + 1:03d}.fits"
            write_fits(path, make_image(rng, 0), INSTRUME=CAMERA, EXPTIME=exposure, GAIN=100, OFFSET=50)
            calibration_total += 1

    star_id = "Vega"
    wavelengths = np.linspace(4000.0, 7500.0, 300)
    intensities = np.exp(-(((wavelengths - 5500.0) / 1800.0) ** 2)) + rng.normal(0, 0.01, wavelengths.size)
    start_time = night_start.astimezone(UTC)
    # The result models are private, so take the empty ones the star record
    # already holds and fill their fields.
    star = astrometrics.stars.create(star_id, ra="18:36:56", dec="+38:47:01")
    photometry = star.photometry
    photometry.timestamps = [start_time + timedelta(minutes=2 * index) for index in range(120)]
    photometry.fluxes = [
        float(1.0e5 * (1 + 0.02 * np.sin(index / 6.0) + rng.normal(0, 0.003))) for index in range(120)
    ]
    photometry.magnitudes = [float(0.03 + 0.02 * np.sin(index / 6.0)) for index in range(120)]
    spectroscopy = star.spectroscopy
    spectroscopy.wavelengths_angstrom = wavelengths.tolist()
    spectroscopy.intensities = intensities.tolist()
    spectroscopy.self_determined_spectral_type = "A0V"
    astrometrics.stars.update(
        star_id,
        {
            "photometry": photometry,
            "spectroscopy": spectroscopy,
            "spectral_type": "A0V",
            "magnitude": 0.03,
            "b_minus_v": 0.0,
            "target_ids": ["M 13"],
        },
    )

    # A second star, so a display's star list has an unselected row.
    second_star_id = "Altair"
    second_star = astrometrics.stars.create(second_star_id, ra="19:50:47", dec="+08:52:06")
    second_spectroscopy = second_star.spectroscopy
    second_spectroscopy.wavelengths_angstrom = wavelengths.tolist()
    second_spectroscopy.intensities = (intensities * 0.8).tolist()
    second_spectroscopy.self_determined_spectral_type = "A7V"
    astrometrics.stars.update(
        second_star_id,
        {
            "spectroscopy": second_spectroscopy,
            "spectral_type": "A7V",
            "magnitude": 0.76,
            "b_minus_v": 0.22,
            "target_ids": ["M 13"],
        },
    )

    # One observing night with guiding samples, and the run that holds them.
    store = ControlRecordStore(str(wayfinding_dir / "wayfinding.db"))
    sample_count = 1200
    first = night_start.timestamp()
    samples = [
        {
            "session_id": NIGHT_DATE,
            "target_name": "M 31",
            "timestamp": first + index * 2.0,
            "dra": float(rng.normal(0, 0.45)),
            "ddec": float(rng.normal(0, 0.35)),
            "pulse_ra": float(rng.normal(0, 20)),
            "pulse_dec": float(rng.normal(0, 15)),
            "snr": 25.0,
            "rms_ra": 0.45,
            "rms_dec": 0.35,
            "star_mass": 8000.0,
            "source": "phd2_guide_log",
        }
        for index in range(sample_count)
    ]
    store.replace_guiding_samples(samples)
    from wayfindinglib.drivers.butler import DiskButler

    butler = DiskButler(config)
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary

    butler.put(
        GuidingRunSummary(
            id=f"guide_log-{NIGHT_DATE}T21-00-00.txt#1",
            session_id=NIGHT_DATE,
            source_file_name=f"guide_log-{NIGHT_DATE}T21-00-00.txt",
            written_by_ekos=False,
            started_at=first,
            ended_at=first + sample_count * 2.0,
            pixel_scale_arcsec_per_px=3.2,
            focal_length_mm=300.0,
            frames_total=sample_count,
            samples_stored=sample_count,
            declination_degrees=41.0,
        ),
        "guiding_run",
        {},
    )
    return {
        "targets": len(TARGETS) * scale,
        "light_frames": frame_total,
        "calibration_frames": calibration_total,
        "guiding_samples": sample_count,
    }


def main() -> int:
    """Seed the library named by the environment, inside the given folder.

    Returns
    -------
    status : `int`
        0 on success, 2 for a usage or safety error.
    """
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    sandbox = Path(sys.argv[1]).resolve()
    config_path = os.environ.get("ASTROMETRICS_CONFIG", "")
    if not config_path or Path(config_path).resolve().parent != sandbox:
        print(
            "Refusing to seed: ASTROMETRICS_CONFIG must name the settings file inside the throwaway folder."
        )
        return 2
    counts = seed(sandbox)
    print("Seeded:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
