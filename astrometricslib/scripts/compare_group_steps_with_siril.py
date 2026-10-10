r"""Compare Siril with our own steps for lining up and combining group stacks.

Stacking by exposure length makes one stack per group, then lines the group
stacks up (`stacking/processing/group_alignment.py`) and combines them
(`combine_exposure_group_images` in `exposure_groups.py`). Siril has commands
that could do similar work. Before replacing one of our steps with Siril's,
this script measures whether the replacement would lose quality. It reads the
group stacks the pipeline already saved (the ``groups`` folder of each target)
and changes nothing in the library.

For each target it makes two comparisons.

1. Alignment. The non-reference group stacks are lined up with the reference
   both ways. The leftover misalignment is measured by fine phase
   correlation, and also by an independent check: the centres of matched
   stars (imaging) or the position of the zero-order star (spectra).
2. Combining. Siril stacks the groups (already lined up by our code, so the
   alignment is not a factor). The result is compared with the pipeline's
   combined stack at the pixels the longest group saturates. Siril has no
   per-pixel saturation handling, so a gap there shows what a replacement
   would lose.

Example::

    python -m astrometricslib.scripts.compare_group_steps_with_siril \
        --targets "M 81" "Moon" "Albireo"
"""

import argparse
import glob
import json
import logging
import os
import shutil
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy import ndimage
from skimage.registration import phase_cross_correlation

from astrometricslib.drivers.fits_access import read_data
from astrometricslib.foundation.config import get_configuration
from astrometricslib.pipelines.stacking.post_processing.stack_preview import run_preview_script
from astrometricslib.pipelines.stacking.processing.exposure_groups import estimate_saturation_mask_level
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION,
    _center_crop,
    _filtered,
    _to_plane,
    align_images_to_reference,
    find_zero_order_position,
)

logger = logging.getLogger(__name__)

# The fewest matched stars for the star-centre check to be reported.
MINIMUM_MATCHED_STARS = 8

# Stars are matched between two images when their centres are closer than
# this many pixels. After either alignment the leftover offset is small, so a
# tight radius avoids pairing different stars.
STAR_MATCH_RADIUS_PIXELS = 3.0

# Phase correlation measures the leftover shift to 1/100 of a pixel.
RESIDUAL_UPSAMPLE_FACTOR = 100

# The longest group must saturate at this many pixels for the combining
# comparison to mean anything.
MINIMUM_SATURATED_PIXELS = 20


def load_plane(path: str) -> np.ndarray:
    """Read a stack as a 2-D float array.

    Parameters
    ----------
    path : `str`
        The FITS stack.

    Returns
    -------
    image : `numpy.ndarray`
        The pixels, averaged over colour planes if there are several.
    """
    data = np.asarray(read_data(path), dtype=np.float32)
    return data if data.ndim == 2 else data.mean(axis=0)


def star_centroids(image: np.ndarray, limit: int = 400) -> np.ndarray:
    """Find the centres of moderately bright, unsaturated stars.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image, 2-D.
    limit : `int`, optional
        The most stars returned, brightest first.

    Returns
    -------
    centroids : `numpy.ndarray`
        One (row, column) pair per star.
    """
    pixels = np.asarray(image, dtype=np.float64)
    smooth = ndimage.gaussian_filter(pixels, 1.2)
    median = np.median(smooth)
    noise = 1.4826 * np.median(np.abs(smooth - median))
    is_peak = (smooth == ndimage.maximum_filter(smooth, 11)) & (smooth > median + 12 * noise)
    ceiling = np.percentile(pixels, 99.999)
    found = []
    for row, column in zip(*np.nonzero(is_peak), strict=True):
        if row < 6 or column < 6 or row > pixels.shape[0] - 7 or column > pixels.shape[1] - 7:
            continue
        cut = pixels[row - 4 : row + 5, column - 4 : column + 5] - median
        if cut.max() > 0.5 * ceiling or cut.min() < -3 * noise:
            continue
        weights = np.clip(cut, 0, None)
        rows, columns = np.mgrid[-4:5, -4:5]
        total = weights.sum()
        found.append((
            row + (weights * rows).sum() / total,
            column + (weights * columns).sum() / total,
            total,
        ))
    found.sort(key=lambda item: -item[2])
    return np.array([(row, column) for row, column, _ in found[:limit]])


def star_offset(reference: np.ndarray, image: np.ndarray) -> tuple[float, float, int] | None:
    """Measure the offset of an image from a reference by matching stars.

    Only stars that are each other's nearest neighbour are used, and the
    median offset is refined once so that a few wrong pairs do not bias it.
    The measure is reliable for offsets under about a pixel, which is what
    is left after an alignment.

    Parameters
    ----------
    reference, image : `numpy.ndarray`
        The two images, 2-D and the same shape.

    Returns
    -------
    offset : `tuple` or `None`
        The median (row, column) offset and the number of stars used, or
        `None` if fewer than `MINIMUM_MATCHED_STARS` could be matched.
    """
    first, second = star_centroids(reference), star_centroids(image)
    if len(first) < MINIMUM_MATCHED_STARS or len(second) < MINIMUM_MATCHED_STARS:
        return None
    distance = np.hypot(first[:, None, 0] - second[None, :, 0], first[:, None, 1] - second[None, :, 1])
    nearest_second, nearest_first = distance.argmin(axis=1), distance.argmin(axis=0)
    mutual = (distance[np.arange(len(first)), nearest_second] < STAR_MATCH_RADIUS_PIXELS) & (
        nearest_first[nearest_second] == np.arange(len(first))
    )
    if mutual.sum() < MINIMUM_MATCHED_STARS:
        return None
    d_row = second[nearest_second[mutual], 0] - first[mutual, 0]
    d_column = second[nearest_second[mutual], 1] - first[mutual, 1]
    keep = np.hypot(d_row - np.median(d_row), d_column - np.median(d_column)) < 1.0
    if keep.sum() < MINIMUM_MATCHED_STARS:
        return None
    return float(np.median(d_row[keep])), float(np.median(d_column[keep])), int(keep.sum())


def correlation_residual(
    reference: np.ndarray, image: np.ndarray, crop_fraction: float | None
) -> tuple[float, float]:
    """Measure the shift left between two images by fine phase correlation.

    Parameters
    ----------
    reference, image : `numpy.ndarray`
        The two images.
    crop_fraction : `float` or `None`
        The central share of each dimension to use, or `None` for all of it.

    Returns
    -------
    shift : `tuple` [`float`, `float`]
        The (row, column) shift of `image` from `reference`.
    """
    reference_plane, image_plane = _to_plane(reference), _to_plane(image)
    if crop_fraction is not None:
        reference_plane = _center_crop(reference_plane, crop_fraction)
        image_plane = _center_crop(image_plane, crop_fraction)
    shift, _, _ = phase_cross_correlation(
        _filtered(reference_plane),
        _filtered(image_plane),
        upsample_factor=RESIDUAL_UPSAMPLE_FACTOR,
        normalization=None,
    )
    return float(shift[0]), float(shift[1])


def zero_order_offset(
    reference: np.ndarray, image: np.ndarray, crop_fraction: float
) -> tuple[float, float] | None:
    """Measure the offset of the zero-order star between two spectral stacks.

    Parameters
    ----------
    reference, image : `numpy.ndarray`
        The two stacks.
    crop_fraction : `float`
        The central share of each dimension searched for the star.

    Returns
    -------
    offset : `tuple` or `None`
        The (row, column) offset, or `None` if either star cannot be found.
    """
    first = find_zero_order_position(_center_crop(_to_plane(reference), crop_fraction))
    second = find_zero_order_position(_center_crop(_to_plane(image), crop_fraction))
    if first is None or second is None:
        return None
    return second[0] - first[0], second[1] - first[1]


def _siril_register(
    images: list[np.ndarray], work: Path, detection: str, siril_executable: str
) -> list[Path]:
    """Register a short list of images with Siril and return its outputs.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The images, reference first.
    work : `pathlib.Path`
        An empty folder under the home folder (the Flatpak sandbox cannot
        see the system temp folder).
    detection : `str`
        The ``setfindstar`` command for star detection.
    siril_executable : `str`
        The command that starts Siril.

    Returns
    -------
    registered : `list` [`pathlib.Path`]
        The registered images that Siril wrote, in order. Empty if it failed.
    """
    shutil.rmtree(work, ignore_errors=True)
    (work / "lights").mkdir(parents=True)
    (work / "process").mkdir()
    for number, image in enumerate(images, start=1):
        fits.PrimaryHDU(np.asarray(image, dtype=np.float32)).writeto(
            work / "lights" / f"light_source_{number:05d}.fits"
        )
    commands = [
        "requires 1.2.0",
        "setext fits",
        "set32bits",
        "cd lights",
        "convert light_source -out=../process",
        "cd ../process",
        detection,
        "register light_source_ -transf=shift",
        "close",
    ]
    run_preview_script(str(work), commands, siril_executable)
    return sorted((work / "process").glob("r_light_source_0*.fits"))


def _siril_blend(images: list[np.ndarray], work: Path, siril_executable: str) -> np.ndarray | None:
    """Stack already-aligned images with Siril, weighted by noise.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The aligned images, reference first.
    work : `pathlib.Path`
        An empty folder under the home folder.
    siril_executable : `str`
        The command that starts Siril.

    Returns
    -------
    blend : `numpy.ndarray` or `None`
        Siril's stack, or `None` if it failed.
    """
    shutil.rmtree(work, ignore_errors=True)
    (work / "lights").mkdir(parents=True)
    (work / "process").mkdir()
    for number, image in enumerate(images, start=1):
        fits.PrimaryHDU(np.asarray(image, dtype=np.float32)).writeto(
            work / "lights" / f"light_source_{number:05d}.fits"
        )
    commands = [
        "requires 1.2.0",
        "setext fits",
        "set32bits",
        "cd lights",
        "convert light_source -out=../process",
        "cd ../process",
        "stack light_source_ rej n 3 3 -norm=addscale -weight=noise -out=blend",
        "close",
    ]
    run_preview_script(str(work), commands, siril_executable)
    blend_path = work / "process" / "blend.fits"
    return np.asarray(load_plane(str(blend_path)), dtype=np.float64) if blend_path.exists() else None


def compare_target(name: str, manifest_path: str, siril_executable: str, work_root: Path) -> None:
    """Run both comparisons for one target and print the results.

    Parameters
    ----------
    name : `str`
        The target's name, for the report.
    manifest_path : `str`
        The group manifest the pipeline saved.
    siril_executable : `str`
        The command that starts Siril.
    work_root : `pathlib.Path`
        The folder for Siril's scratch files.
    """
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    groups = [
        group
        for group in manifest["groups"]
        if group.get("stack_path")
        and os.path.exists(group["stack_path"])
        and not group.get("left_out_reason")
    ]
    if len(groups) < 2:
        print(f"\n=== {name}: needs at least two usable groups")
        return
    spectral = "_SPEC_" in os.path.basename(manifest_path)
    crop = SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION if spectral else None
    combined_path = os.path.join(os.path.dirname(os.path.dirname(manifest_path)), manifest["combined_stack"])
    images = [load_plane(group["stack_path"]) for group in groups]
    reference = int(np.argmax([group["weight"] for group in groups]))
    order = [reference] + [index for index in range(len(groups)) if index != reference]
    seconds = [group["exposure_seconds"] for group in groups]
    print(f"\n=== {name}: {len(groups)} groups {seconds} s, reference = {seconds[reference]} s")
    aligned, covered, results = align_images_to_reference(
        images, reference, crop_fraction=crop, prefer_star_position=spectral
    )

    detections = {"standard": "setfindstar -relax=on"}
    if spectral:
        detections["relaxed"] = "setfindstar -relax=on -roundness=0.15 -radius=3"
    siril_outputs = {}
    for label, command in detections.items():
        work = work_root / name.replace(" ", "_") / label
        siril_outputs[label] = _siril_register([images[i] for i in order], work, command, siril_executable)
        print(f"  Siril register ({label}): {len(siril_outputs[label])} of {len(order)} frames written")

    def report(label: str, image: np.ndarray) -> None:
        """Print the leftover misalignment of one aligned image."""
        shift = correlation_residual(images[reference], image, crop)
        if spectral:
            zero = zero_order_offset(images[reference], image, crop)
            extra = (
                f"zero-order star offset ({zero[0]:+.2f}, {zero[1]:+.2f})"
                if zero
                else "zero-order star not found"
            )
        else:
            offset = star_offset(images[reference], image)
            extra = (
                f"star-centre offset ({offset[0]:+.2f}, {offset[1]:+.2f}), {offset[2]} stars"
                if offset
                else "too few stars"
            )
        print(f"       {label:10s}: phase-correlation residual ({shift[0]:+.2f}, {shift[1]:+.2f}); {extra}")

    for index in range(len(groups)):
        if index == reference:
            continue
        print(f"  group {seconds[index]} s against the reference:")
        result = results[index]
        if aligned[index] is None:
            print(f"     ours: could not line up (correlation {result.correlation:.2f})")
        else:
            print(
                f"     ours: shift ({result.shift_rows_pixels:+.2f}, {result.shift_columns_pixels:+.2f}) px, "
                f"correlation {result.correlation:.2f}"
            )
        report("unaligned", images[index])
        if aligned[index] is not None:
            report("ours", aligned[index])
        for label, outputs in siril_outputs.items():
            written = [p for p in outputs if p.name.endswith(f"_{order.index(index) + 1:05d}.fits")]
            if written:
                report(f"Siril {label[:4]}", load_plane(str(written[0])))
            else:
                print(
                    f"       Siril {label[:4]:4s}  : no registered image (registration failed or dropped it)"
                )

    if any(image is None for image in aligned):
        print("  combining: skipped because not every group could be lined up")
        return
    blend = _siril_blend(
        [aligned[i] for i in order], work_root / name.replace(" ", "_") / "combine", siril_executable
    )
    if blend is None or not os.path.exists(combined_path):
        print("  combining: Siril's stack or the pipeline's combined stack is missing")
        return
    ours = np.asarray(load_plane(combined_path), dtype=np.float64)
    longest = int(np.argmax(seconds))
    ceiling = estimate_saturation_mask_level(images[longest], 0.98)
    saturated = (aligned[longest] >= ceiling) & covered[longest].astype(bool)
    if saturated.sum() < MINIMUM_SATURATED_PIXELS:
        print(f"  combining: only {int(saturated.sum())} pixels saturate in the longest group")
        return
    near = ndimage.binary_dilation(saturated, iterations=2)
    usable = (~near) & (ours > np.percentile(ours, 90)) & (ours < np.percentile(ours[~near], 99.5))
    scale = np.median(ours[usable] / np.where(blend[usable] == 0, np.nan, blend[usable]))
    ratio = blend[saturated] * scale / np.where(ours[saturated] == 0, np.nan, ours[saturated])
    ratio = ratio[np.isfinite(ratio)]
    print(f"  combining: {int(saturated.sum())} pixels saturate in the {seconds[longest]} s group")
    print(
        f"     Siril blend / pipeline stack there: median {np.median(ratio):.2f}, "
        f"10th-90th percentile {np.percentile(ratio, 10):.2f}-{np.percentile(ratio, 90):.2f}"
    )
    print(f"     peak value: pipeline {ours.max():.3f}, Siril blend (scaled) {blend.max() * scale:.3f}")


def find_manifests(library_lights: str, target: str) -> list[str]:
    """Find a target's group manifests.

    Parameters
    ----------
    library_lights : `str`
        The library's ``lights`` folder.
    target : `str`
        The target name, as its folder is named.

    Returns
    -------
    manifests : `list` [`str`]
        The manifest paths, newest first.
    """
    pattern = os.path.join(library_lights, target, "groups", "*_manifest.json")
    return sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)


def main(arguments: list[str] | None = None) -> int:
    """Run the script.

    Parameters
    ----------
    arguments : `list` [`str`], optional
        The command line. Defaults to `sys.argv`.

    Returns
    -------
    status : `int`
        0 on success, 1 if no group manifest was found.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--targets", nargs="+", required=True, metavar="NAME", help="Target folder names.")
    parser.add_argument(
        "--work-dir",
        default=os.path.join(os.path.expanduser("~"), "Siril", "Work", "group_step_comparison"),
        help="Scratch folder for Siril. Must be under the home folder.",
    )
    options = parser.parse_args(arguments)
    configuration = get_configuration()
    siril_executable = configuration.get_siril_executable()
    if not siril_executable:
        print("No Siril executable is configured.", file=sys.stderr)
        return 1
    library_lights = os.path.join(str(configuration.get_frames_path()), "lights")
    found = False
    for target in options.targets:
        for manifest_path in find_manifests(library_lights, target):
            found = True
            label = f"{target} ({os.path.basename(manifest_path).replace('_manifest.json', '')})"
            compare_target(label, manifest_path, siril_executable, Path(options.work_dir))
    if not found:
        print("No group manifest found for the named targets.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
