"""Chooses, stores and reloads the fixed empty apertures of a target's noise.

To compare the noise of two stacks of one target, every stack must be measured
in the same patches of sky. This module holds everything that picks and keeps
those patches (it does not measure any noise):

* `choose_apertures` and `choose_apertures_with_reason` pick up to 100 empty
  apertures and 10 bright, isolated reference stars on one stack, as sky
  positions (right ascension and declination, not pixels).
* `place_empty_apertures` draws the aperture positions. They stay clear of the
  edge, of bright stars found by the star finder, and of faint sources found
  by a mask made from the stack's own pixels (`analyze_faint_sources` and
  `faint_source_distance`), so a patch is chosen by a pixel mask made before
  placement, never by its own sum. The mask is not perfect: a 3.5-sigma noise
  peak near a patch can exclude it, so the noise is biased low by a small
  amount that was measured (validation entry STK-36).
* `ApertureSet` is one chosen set, `StackDepth` holds the frame counts of the
  stack that chose it, and a set is replaced when a library stack has at least
  `REPLACEMENT_DEPTH_FACTOR` times the effective frame count.
* `load_apertures`, `load_apertures_with_reason` and `reset_apertures` read
  and forget the small file beside the target's stacks
  (``quality_apertures.json``) that keeps one set for each camera, optic and
  reference exposure length (see `ApertureSet.setup_key`). A stack made with
  other equipment, or lined up on a frame of another exposure length, starts
  a new set. Noise values measured with different sets are not comparable.

It imports `stack_image` only; `stack_noise.py` and `difference_noise.py`
import it. The estimator and the numbers are declared in
`pipelines/stacking/metrics.py`.
"""

import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree

from astrometricslib.foundation.paths import locks_directory
from astrometricslib.foundation.storage.process_locks import file_lock
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    APERTURE_RADIUS_PX,
    MAD_TO_SIGMA,
    RING_INNER_PX,
    RING_OUTER_PX,
    SaturationCheck,
    _pixel_scale_arcsec,
    _pixels_to_sky,
    signed_aperture_sum,
)
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids

logger = logging.getLogger(__name__)


# The hash in an identity has 12 hex digits, which is 48 bits. A stored row
# keeps the identity as one number, the file version times 2**48 plus the
# hash. A 64-bit float holds every integer below 2**53 exactly, so the file
# version can be at most 31 (2**53 / 2**48 is 32).
_IDENTITY_HASH_BITS = 48
_IDENTITY_HASH_DIGITS = 12
_MAXIMUM_IDENTITY_FILE_VERSION = 31


def aperture_identity_to_number(identity: str) -> float:
    """Turn an aperture-set identity into one exact number for a stored row.

    The quality store keeps numbers, not text, as a row's value. The number
    is the file version times 2**48 plus the hash read as a hexadecimal
    number. It is below 2**53, so a 64-bit float holds it exactly.

    Parameters
    ----------
    identity : `str`
        An identity such as ``"v3-1a2b3c4d5e6f"`` (see `ApertureSet.identity`).

    Returns
    -------
    number : `float`
        A whole number stored in a float.

    Raises
    ------
    ValueError
        If the text is not a version, a dash and 12 hexadecimal digits, or
        if the version is above 31.
    """
    prefix, dash, digest = identity.partition("-")
    if not (dash and prefix[:1] == "v" and prefix[1:].isdigit() and len(digest) == _IDENTITY_HASH_DIGITS):
        raise ValueError(f"not an aperture-set identity: {identity!r}")
    version = int(prefix[1:])
    if version > _MAXIMUM_IDENTITY_FILE_VERSION:
        raise ValueError(f"aperture file version {version} does not fit an exact stored number")
    return float((version << _IDENTITY_HASH_BITS) + int(digest, 16))


def aperture_identity_from_number(number: float) -> str:
    """Turn a stored aperture-set number back into the identity text.

    This undoes `aperture_identity_to_number`. Two stored rows were measured
    in the same apertures exactly when this gives the same text for both.

    Parameters
    ----------
    number : `float`
        The value of a ``stacking.aperture_set_number`` row.

    Returns
    -------
    identity : `str`
        For example ``"v3-1a2b3c4d5e6f"``.

    Raises
    ------
    ValueError
        If the number is not a whole number from 0 up to 2**53.
    """
    if not math.isfinite(number) or number != int(number) or not 0 <= number < 2**53:
        raise ValueError(f"not a stored aperture-set number: {number!r}")
    whole = int(number)
    version, digest = whole >> _IDENTITY_HASH_BITS, whole & ((1 << _IDENTITY_HASH_BITS) - 1)
    return f"v{version}-{digest:0{_IDENTITY_HASH_DIGITS}x}"


__all__ = [
    "APERTURE_FILE_NAME",
    "ApertureSet",
    "ApertureSetMismatchError",
    "FaintSourceAnalysis",
    "StackDepth",
    "analyze_faint_sources",
    "aperture_identity_from_number",
    "aperture_identity_to_number",
    "choose_apertures",
    "choose_apertures_with_reason",
    "faint_source_distance",
    "find_library_set_key",
    "is_legacy_setup_key",
    "load_apertures",
    "load_apertures_with_reason",
    "place_empty_apertures",
    "record_library_set_key",
    "reset_apertures",
]

# The file beside a target's stacks that keeps its sky apertures.
APERTURE_FILE_NAME = "quality_apertures.json"

# The version of the file's layout. Version 3 holds one set of apertures for
# each camera, optic and reference exposure, placed away from faint sources
# as well as bright
# stars (see `FAINT_SOURCE_THRESHOLD_SIGMAS`), with the depth of the stack that
# defined it. Version 2 apertures were placed away from the 400 brightest stars
# only; faint stars sat in them. Version 1 held one set, with no per-star
# reference brightness. Older files are replaced when a library stack is
# measured, never used, and a subset stack refuses them with a reason.
APERTURE_FILE_VERSION = 3

# The reason a subset stack gives when the stored apertures are missing or of
# an older version: a subset never chooses apertures, so that every subset of a
# target is read in the apertures its library stack chose.
OLD_APERTURES_REASON = (
    f"the target's apertures predate version {APERTURE_FILE_VERSION}; "
    "stack the target once so its library stack chooses them"
)

# A stored set is replaced when a library stack has at least this many times
# the defining stack's effective frame count. Noise falls as 1 over the square
# root of the frame count, so 2 times the frames is 1.41 times less noise:
# sources 1.4 times fainter then stand above the noise and the older mask
# misses them. Smaller factors would change the set too often to compare
# stacks of a growing target; this is a design choice (see STK-36).
REPLACEMENT_DEPTH_FACTOR = 2.0

# The setup key of a stack whose header names neither camera, telescope nor
# focal length.
UNSPECIFIED_SETUP = "unspecified"

# The text before the reference exposure length at the end of a setup key.
# Keys written before the exposure was added end without it, and are never
# matched (see `is_legacy_setup_key`).
EXPOSURE_KEY_MARKER = "|exp="

# The number of empty apertures. The spread of n samples has a relative error
# of about 1.17 / sqrt(n) (see `MAD_SIGMA_ERROR_FACTOR`): about 12% for 100.
# More would cost little but the stack must have the room; a typical stack
# holds tens of thousands of apertures of this size, so 100 do not crowd it.
# A design choice.
NOISE_APERTURE_COUNT = 100


# The reference stars are measured in a larger aperture than the noise, so
# that their brightness does not change when the seeing changes. The radius is
# 3 times the star width (FWHM) of the stack that chose the stars: a round
# Gaussian star of that width has all but about 1e-11 of its light inside it,
# and a star with wings (a Moffat profile) keeps about 95% inside. The sky ring
# starts 3 px beyond the aperture and is 5 px wide, the same gap and width as
# the noise ring has around the noise aperture. The radius is never below
# 8 px (twice the noise aperture's), which a star width under 2.7 px would
# otherwise give.
REFERENCE_APERTURE_FWHM_MULTIPLE = 3.0
REFERENCE_APERTURE_MINIMUM_RADIUS_PX = 8.0
REFERENCE_RING_GAP_PX = RING_INNER_PX - APERTURE_RADIUS_PX
REFERENCE_RING_WIDTH_PX = RING_OUTER_PX - RING_INNER_PX

# How many reference stars fix the flux scale, and the fewest that must be
# measurable on a stack. The median of 10 stars is steady against one star that
# varies or has a neighbor; 5 is the smallest set that still has a median.
REFERENCE_STAR_COUNT = 10
MINIMUM_USABLE_REFERENCE_STARS = 5

# A stack needs this many usable apertures (20 gives a 28% error on the
# spread).
MINIMUM_USABLE_APERTURES = 20

# An empty aperture stays this far from every detected star, so a star's wings
# cannot reach the aperture or its ring: twice the ring's outer radius.
STAR_CLEARANCE_PX = 2.0 * RING_OUTER_PX

# Faint sources are kept out of the empty apertures by a mask made from the
# stack itself, before any aperture is drawn (so the rule never looks at an
# aperture's sum). The stack is high-pass filtered (the Gaussian-smoothed
# image at this width is taken away, about the ring's outer radius, so a
# smooth sky gradient or a broad nebula halo is not flagged), then smoothed at
# each width below (a faint star is the size of the star width, 1 to 2 px; a
# small galaxy or a knot of nebula is several pixels), and every pixel more
# than `FAINT_SOURCE_THRESHOLD_SIGMAS` robust sigmas above zero at any width is
# flagged. The sigma of each width is the median absolute deviation of that
# smoothed image over the pixels that hold data (not the empty edge filled
# with the median, which would pull the scale down), which stars cover too
# little of to move it, and which keeps the correlation of the stacked noise
# that smoothing does not remove. A crowded field raises the scale and so the
# effective threshold (validation entry STK-36 gives the measured rise).
FAINT_SOURCE_HIGH_PASS_SIGMA_PX = 12.0
FAINT_SOURCE_SMOOTHING_SIGMAS_PX = (1.5, 3.0, 6.0)

# A smoothed pixel is flagged above this many robust sigmas. For pure noise a
# bell curve passes 3.5 sigma in 2.3e-4 of its pixels, so a few apertures in a
# hundred lose a position to noise alone; the measured loss in the noise
# (a downward bias) is in validation entry STK-36. A design choice, checked
# on a synthetic noise field and on the M 57 stack.
FAINT_SOURCE_THRESHOLD_SIGMAS = 3.5

# An aperture stays this far from every flagged pixel (measured from the
# rounded pixel of its center): the ring's outer radius plus 1 px. Rounding the
# center to a pixel moves it by up to 0.71 px, and the ring reaches the pixels
# whose centers lie within the outer radius, so 1 px more guarantees that no
# flagged pixel lies in the aperture or in its sky ring.
FAINT_SOURCE_CLEARANCE_PX = RING_OUTER_PX + 1.0

# Two apertures stay at least this far apart: twice the ring's outer radius.
# Then no two rings share a pixel, so the sky level taken for one aperture
# does not feed another, and the sums are independent of each other.
APERTURE_SEPARATION_PX = 2.0 * RING_OUTER_PX

# Apertures stay this far from the edge so the whole ring is on the image.
EDGE_MARGIN_PX = math.ceil(RING_OUTER_PX) + 1

# How far an empty aperture stays from a pixel that one of the exposure groups
# of a group stack does not cover (its shifted border; see
# `ExposureGroupCombination.covered_masks`). The combine leaves a group out
# where its mask is empty, so the weights, and the noise, change across that
# border; the aperture's sky ring must not reach it. The outer ring radius
# plus one pixel is the same reach `signed_aperture_sum` keeps from the image
# edge. Design choice, not tuned.
UNCOVERED_CLEARANCE_PX = RING_OUTER_PX + 1.0


# How many stars to detect when choosing apertures and the reference stars.
DETECTED_STAR_COUNT = 400

# Random positions are drawn from a generator with this seed, so choosing
# apertures twice on the same stack gives the same apertures.
APERTURE_SEED = 20261023


class ApertureSetMismatchError(ValueError):
    """Two noise measurements were made in different sets of apertures."""


@dataclass(frozen=True)
class StackDepth:
    """How many frames a stack holds, for the rule that replaces old apertures.

    Parameters
    ----------
    frame_count : `int`
        The number of frames in the stack.
    effective_frame_count : `float`
        Its effective frame count, (sum of w)^2 / (sum of w^2).
    """

    frame_count: int
    effective_frame_count: float


def _rounded_or_none(value: float | None) -> float | None:
    """Round a frame count to 3 decimals, so a file round trip keeps its hash.

    Parameters
    ----------
    value : `float` or `None`
        The effective frame count.

    Returns
    -------
    rounded : `float` or `None`
        The rounded value, or `None`.
    """
    return None if value is None else round(float(value), 3)


def _optional_number(value: object, kind: type) -> int | float | None:
    """Turn a stored JSON value into a number, or `None` when it is absent.

    Parameters
    ----------
    value : `object`
        What the file held: a number or `None`.
    kind : `type`
        `int` or `float`.

    Returns
    -------
    number : `int`, `float` or `None`
        The converted value.
    """
    return None if value is None else kind(value)


@dataclass(frozen=True)
class ApertureSet:
    """The sky positions a target's stack noise is measured at.

    Parameters
    ----------
    aperture_sky : `numpy.ndarray`
        Shape ``(n, 2)``: right ascension and declination of each empty
        aperture, in degrees.
    reference_sky : `numpy.ndarray`
        Shape ``(k, 2)``: right ascension and declination of each reference
        star, in degrees, brightest first.
    wcs_source : `str`
        Where the coordinate solution came from when the set was chosen:
        ``"stack header"`` or ``"registration reference frame"``.
    pixel_scale_arcsec : `float`
        The pixel scale of the stack the set was chosen on.
    created_from : `str`
        The name of that stack's file.
    reference_baseline_flux : `numpy.ndarray`
        Shape ``(k,)``: each reference star's brightness (sky-subtracted sum
        in the large reference aperture) on the stack that chose it. Later
        stacks are compared with these numbers.
    reference_radius_px : `float`
        The radius of the large reference aperture.
    reference_fwhm_px : `float`
        The star width of the stack that chose the set, which sized the
        reference aperture.
    saturation_source : `str`
        How saturated stars were kept out when the set was chosen.
    setup_key : `str`
        The camera, telescope, focal length and reference exposure length the
        set belongs to (see `_setup_key`).
    defining_frame_count : `int` or `None`
        The number of frames in the stack that chose the set; `None` when the
        caller did not know it.
    defining_effective_frame_count : `float` or `None`
        That stack's effective frame count (Kish's N_eff); `None` when not
        known. A set with no effective count is never replaced for depth.
    mask_noise_scales : `tuple` [`float`, ...]
        The robust noise scale of the faint-source mask at each smoothing of
        `FAINT_SOURCE_SMOOTHING_SIGMAS_PX`, divided by the median brightness of
        the reference stars on the defining stack, so it is in the stack's
        normalized units.
    flagged_fraction : `float` or `None`
        The share of the defining stack's pixels the mask flagged.
    excluded_fraction : `float` or `None`
        The share within `FAINT_SOURCE_CLEARANCE_PX` of a flagged pixel, where
        no aperture could be placed.
    """

    aperture_sky: np.ndarray
    reference_sky: np.ndarray
    wcs_source: str
    pixel_scale_arcsec: float
    created_from: str
    reference_baseline_flux: np.ndarray
    reference_radius_px: float
    reference_fwhm_px: float
    saturation_source: str
    setup_key: str = UNSPECIFIED_SETUP
    defining_frame_count: int | None = None
    defining_effective_frame_count: float | None = None
    mask_noise_scales: tuple[float, ...] = ()
    flagged_fraction: float | None = None
    excluded_fraction: float | None = None

    @property
    def identity(self) -> str:
        """Give the set's identity: the file version and a hash of the set.

        Two noise measurements may be compared only when they were made in
        apertures with the same identity. The hash covers the aperture
        positions rounded to 1e-6 degrees (0.004 arcsec), so it is the same
        after the positions go through the JSON file and back, the reference
        stars' positions rounded the same way, the setup key (camera, optic
        and reference exposure) and the defining stack's frame counts. So a
        set chosen again on a deeper stack, or on a reference frame of
        another exposure length, has a new identity even if the same
        positions were drawn.

        Returns
        -------
        identity : `str`
            For example ``"v3-1a2b3c4d5e6f"``.
        """
        rounded = np.round(np.asarray(self.aperture_sky, dtype="<f8"), 6)
        reference_rounded = np.round(np.asarray(self.reference_sky, dtype="<f8"), 6)
        depth = f"{self.defining_frame_count}|{_rounded_or_none(self.defining_effective_frame_count)}"
        digest = hashlib.sha1(
            rounded.tobytes() + reference_rounded.tobytes() + f"{self.setup_key}|{depth}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:12]
        return f"v{APERTURE_FILE_VERSION}-{digest}"

    def describe_definition(self) -> str:
        """Say which stack defined the set and how deep it was.

        Returns
        -------
        sentence : `str`
            The identity, the frame counts and the mask's flagged share, for
            the note that goes with every stored noise row.
        """
        if self.defining_frame_count is None and self.defining_effective_frame_count is None:
            depth = "the defining stack's frame count is unknown"
        else:
            frames = "unknown" if self.defining_frame_count is None else str(self.defining_frame_count)
            effective = (
                "unknown"
                if self.defining_effective_frame_count is None
                else f"{self.defining_effective_frame_count:.1f}"
            )
            depth = f"defined by a stack of {frames} frames (effective {effective})"
        share = (
            ""
            if self.flagged_fraction is None or self.excluded_fraction is None
            else (
                f"; mask flagged {100 * self.flagged_fraction:.0f}% and excluded "
                f"{100 * self.excluded_fraction:.0f}%"
            )
        )
        return f"Apertures {self.identity}: {depth}{share}."

    @property
    def reference_ring_inner_px(self) -> float:
        """Give the inner radius of the reference stars' sky ring.

        Returns
        -------
        radius : `float`
            Pixels.
        """
        return self.reference_radius_px + REFERENCE_RING_GAP_PX

    @property
    def reference_ring_outer_px(self) -> float:
        """Give the outer radius of the reference stars' sky ring.

        Returns
        -------
        radius : `float`
            Pixels.
        """
        return self.reference_ring_inner_px + REFERENCE_RING_WIDTH_PX

    def to_json(self) -> dict:
        """Turn the set into plain JSON data.

        Returns
        -------
        data : `dict`
            The set, with every number a plain `float`.
        """
        return {
            "aperture_radius_px": APERTURE_RADIUS_PX,
            "ring_inner_px": RING_INNER_PX,
            "ring_outer_px": RING_OUTER_PX,
            "wcs_source": self.wcs_source,
            "pixel_scale_arcsec": self.pixel_scale_arcsec,
            "created_from": self.created_from,
            "setup_key": self.setup_key,
            "apertures_ra_dec_deg": self.aperture_sky.tolist(),
            "reference_stars_ra_dec_deg": self.reference_sky.tolist(),
            "reference_baseline_flux": self.reference_baseline_flux.tolist(),
            "reference_radius_px": self.reference_radius_px,
            "reference_fwhm_px": self.reference_fwhm_px,
            "saturation_source": self.saturation_source,
            "identity": self.identity,
            "defining_frame_count": self.defining_frame_count,
            "defining_effective_frame_count": self.defining_effective_frame_count,
            "mask_noise_scales_normalized": list(self.mask_noise_scales),
            "flagged_fraction": self.flagged_fraction,
            "excluded_fraction": self.excluded_fraction,
        }

    @classmethod
    def from_json(cls, data: dict) -> ApertureSet:
        """Rebuild a set from `to_json` data.

        Parameters
        ----------
        data : `dict`
            What `to_json` returned.

        Returns
        -------
        aperture_set : `ApertureSet`
            The set.
        """
        return cls(
            aperture_sky=np.asarray(data["apertures_ra_dec_deg"], dtype=float).reshape(-1, 2),
            reference_sky=np.asarray(data["reference_stars_ra_dec_deg"], dtype=float).reshape(-1, 2),
            wcs_source=str(data["wcs_source"]),
            pixel_scale_arcsec=float(data["pixel_scale_arcsec"]),
            created_from=str(data["created_from"]),
            reference_baseline_flux=np.asarray(data["reference_baseline_flux"], dtype=float).ravel(),
            reference_radius_px=float(data["reference_radius_px"]),
            reference_fwhm_px=float(data["reference_fwhm_px"]),
            saturation_source=str(data["saturation_source"]),
            setup_key=str(data.get("setup_key", UNSPECIFIED_SETUP)),
            defining_frame_count=_optional_number(data.get("defining_frame_count"), int),
            defining_effective_frame_count=_optional_number(
                data.get("defining_effective_frame_count"), float
            ),
            mask_noise_scales=tuple(float(value) for value in data.get("mask_noise_scales_normalized", ())),
            flagged_fraction=_optional_number(data.get("flagged_fraction"), float),
            excluded_fraction=_optional_number(data.get("excluded_fraction"), float),
        )


def _equipment_key(*headers: fits.Header) -> str:
    """Name the camera and optic a stack was made with.

    Parameters
    ----------
    *headers : `astropy.io.fits.Header`
        The stack's header, then the registration reference frame's, if any.
        The first one that names a camera, a telescope or a focal length is
        used.

    Returns
    -------
    key : `str`
        ``camera|telescope|focal length`` in lower case, with ``?`` for a
        missing part, or `UNSPECIFIED_SETUP` when no header names any.
    """
    for header in headers:
        camera = str(header.get("INSTRUME", "")).strip().lower()
        telescope = str(header.get("TELESCOP", "")).strip().lower()
        focal_length = header.get("FOCALLEN")
        if camera or telescope or focal_length is not None:
            focal_text = f"{float(focal_length):g}" if isinstance(focal_length, int | float) else "?"
            return f"{camera or '?'}|{telescope or '?'}|{focal_text}"
    return UNSPECIFIED_SETUP


def _setup_key(*headers: fits.Header, reference_exposure_seconds: float | None = None) -> str:
    """Name the camera, optic and reference exposure of a stack.

    The exposure length is the one of the frame the stack was lined up on
    (for a stack of exposure groups, the heaviest group's). It is part of the
    key, and a set is not shared between exposure lengths, for two reasons.
    The set's reference stars are chosen so that none is saturated in the
    reference frame; a longer exposure saturates stars a shorter one does not,
    and then no reference star can be used (the noise is not measured). And a
    star's brightness in the set is its brightness on the stack that chose it,
    which depends on how the stack was weighted. One set per exposure length
    is kept, not one that is overwritten, so a later stack with the same
    exposure length is still compared in the same patches of sky as before.

    Parameters
    ----------
    *headers : `astropy.io.fits.Header`
        The stack's header, then the registration reference frame's, if any.
        See `_equipment_key`.
    reference_exposure_seconds : `float`, optional
        The exposure length of the reference frame, in seconds. Left out when
        it is not known; the key then ends in ``?``.

    Returns
    -------
    key : `str`
        ``camera|telescope|focal length|exp=<seconds>`` in lower case, for
        example ``zwo asi533mm pro|?|400|exp=120``.
    """
    if reference_exposure_seconds is None or not math.isfinite(reference_exposure_seconds):
        exposure_text = "?"
    else:
        exposure_text = f"{reference_exposure_seconds:g}"
    return f"{_equipment_key(*headers)}{EXPOSURE_KEY_MARKER}{exposure_text}"


def is_legacy_setup_key(setup_key: str) -> bool:
    """Say whether a stored key was written before the exposure was part of it.

    Parameters
    ----------
    setup_key : `str`
        A key from the aperture file.

    Returns
    -------
    legacy : `bool`
        `True` when the key has no reference exposure part. Such a set was
        chosen on a frame of an unknown exposure length, so no stack is
        measured in it; the next library stack chooses a new set, once.
    """
    return EXPOSURE_KEY_MARKER not in setup_key


def _equipment_of_key(setup_key: str) -> str:
    """Give the camera and optic part of a setup key.

    Parameters
    ----------
    setup_key : `str`
        A key from the aperture file.

    Returns
    -------
    equipment : `str`
        The key without its exposure part.
    """
    return setup_key.split(EXPOSURE_KEY_MARKER, 1)[0]


def choose_apertures(
    plane: np.ndarray,
    wcs: WCS,
    wcs_source: str,
    created_from: str,
    setup_key: str = UNSPECIFIED_SETUP,
    saturation: SaturationCheck | None = None,
    depth: StackDepth | None = None,
    covered_mask: np.ndarray | None = None,
) -> ApertureSet | None:
    """Choose empty-sky apertures and reference stars on one stack.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    wcs_source : `str`
        Where the solution came from, kept with the set.
    created_from : `str`
        The stack's file name, kept with the set.
    setup_key : `str`, optional
        The camera, optic and reference exposure the set belongs to.
    saturation : `SaturationCheck`, optional
        Keeps stars that are saturated in the registration reference frame
        out of the reference stars. Left out, the stack's own 99.99th
        percentile screens the brightest stars instead.
    depth : `StackDepth`, optional
        The stack's frame counts, kept with the set. A set without them is
        never replaced for depth.
    covered_mask : `numpy.ndarray`, optional
        `True` where every exposure group of a group stack has data (see
        `place_empty_apertures`).

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        See `choose_apertures_with_reason`.
    """
    return choose_apertures_with_reason(
        plane, wcs, wcs_source, created_from, setup_key, saturation, depth, covered_mask
    )[0]


def choose_apertures_with_reason(
    plane: np.ndarray,
    wcs: WCS,
    wcs_source: str,
    created_from: str,
    setup_key: str = UNSPECIFIED_SETUP,
    saturation: SaturationCheck | None = None,
    depth: StackDepth | None = None,
    covered_mask: np.ndarray | None = None,
) -> tuple[ApertureSet | None, str]:
    """Choose empty-sky apertures and reference stars, and say why not if none.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    wcs_source : `str`
        Where the solution came from, kept with the set.
    created_from : `str`
        The stack's file name, kept with the set.
    setup_key : `str`, optional
        The camera, optic and reference exposure the set belongs to.
    saturation : `SaturationCheck`, optional
        Keeps stars that are saturated in the registration reference frame
        out of the reference stars. Left out, the stack's own 99.99th
        percentile screens the brightest stars instead.
    depth : `StackDepth`, optional
        The stack's frame counts, kept with the set.
    covered_mask : `numpy.ndarray`, optional
        `True` where every exposure group of a group stack has data, on the
        same pixel grid as `plane`. An aperture is kept at least
        `UNCOVERED_CLEARANCE_PX` from any pixel where it is `False`, because
        a group that does not reach a pixel changes how the others are
        weighted there.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The apertures (positions drawn from a generator with a fixed seed, at
        least `APERTURE_SEPARATION_PX` apart, at least `STAR_CLEARANCE_PX`
        from every detected star, at least `FAINT_SOURCE_CLEARANCE_PX` from
        every faint source found by `faint_source_distance`, and kept only
        where `signed_aperture_sum` works) and the brightest
        `REFERENCE_STAR_COUNT` isolated stars that are not saturated. `None`
        when fewer than `MINIMUM_USABLE_APERTURES` apertures or
        `MINIMUM_USABLE_REFERENCE_STARS` stars could be found.
    reason : `str`
        Why there is no set. Empty when there is one.
    """
    fwhm = measure_fwhm_from_data(plane)
    if fwhm is None or not math.isfinite(fwhm) or fwhm <= 0:
        return None, "the stack's star width could not be measured, so the reference aperture has no size"
    radius = max(REFERENCE_APERTURE_FWHM_MULTIPLE * fwhm, REFERENCE_APERTURE_MINIMUM_RADIUS_PX)
    ring_inner = radius + REFERENCE_RING_GAP_PX
    ring_outer = ring_inner + REFERENCE_RING_WIDTH_PX
    # Every bright star counts for the clearance and the isolation, saturated
    # or not: a saturated star has the widest wings.
    stars = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    analysis = analyze_faint_sources(plane)
    positions = place_empty_apertures(plane, stars, analysis.distance, covered_mask)
    if len(positions) < MINIMUM_USABLE_APERTURES:
        return None, (
            f"only {len(positions)} empty apertures could be placed on this stack, away from bright stars "
            f"and from faint sources ({FAINT_SOURCE_THRESHOLD_SIGMAS} sigma or more above the sky at a "
            f"smoothing of {FAINT_SOURCE_SMOOTHING_SIGMAS_PX} px); the mask flagged "
            f"{100 * analysis.flagged_fraction:.0f}% of the image and its clearance excludes "
            f"{100 * analysis.excluded_fraction:.0f}% of it; at least "
            f"{MINIMUM_USABLE_APERTURES} are needed"
        )
    percentile_screen = None
    if saturation is None:
        percentile_screen = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=True)
    reference: list[tuple[float, float]] = []
    baseline: list[float] = []
    for row, column in _isolated_stars(stars, clearance=max(STAR_CLEARANCE_PX, ring_outer + 1.0)):
        if saturation is not None and saturation.is_saturated(row, column):
            continue
        if percentile_screen is not None and (
            len(percentile_screen) == 0 or np.min(np.hypot(*(percentile_screen - (row, column)).T)) > 1.0
        ):
            continue
        flux = signed_aperture_sum(plane, column, row, radius, ring_inner, ring_outer)
        if flux is None or flux <= 0:
            continue
        reference.append((row, column))
        baseline.append(flux)
        if len(reference) >= REFERENCE_STAR_COUNT:
            break
    if len(reference) < MINIMUM_USABLE_REFERENCE_STARS:
        return (
            None,
            f"only {len(reference)} isolated, unsaturated reference stars could be found on this stack",
        )
    median_baseline = float(np.median(baseline))
    mask_scales = tuple(scale / median_baseline for scale in analysis.noise_scales)
    return (
        ApertureSet(
            aperture_sky=_pixels_to_sky(wcs, np.array(positions, dtype=float)),
            reference_sky=_pixels_to_sky(wcs, np.array(reference, dtype=float)),
            wcs_source=wcs_source,
            pixel_scale_arcsec=_pixel_scale_arcsec(wcs),
            created_from=created_from,
            reference_baseline_flux=np.array(baseline, dtype=float),
            reference_radius_px=float(radius),
            reference_fwhm_px=float(fwhm),
            saturation_source=(
                saturation.source
                if saturation is not None
                else "the stack's own 99.99th percentile (no registration reference frame was available)"
            ),
            setup_key=setup_key,
            defining_frame_count=None if depth is None else depth.frame_count,
            defining_effective_frame_count=None if depth is None else depth.effective_frame_count,
            mask_noise_scales=mask_scales,
            flagged_fraction=analysis.flagged_fraction,
            excluded_fraction=analysis.excluded_fraction,
        ),
        "",
    )


def place_empty_apertures(
    plane: np.ndarray,
    stars: np.ndarray,
    source_distance: np.ndarray | None = None,
    covered_mask: np.ndarray | None = None,
) -> list[tuple[float, float]]:
    """Draw empty-sky aperture positions, away from stars and faint sources.

    Positions are drawn from a generator with a fixed seed. A draw is kept
    when it is at least `EDGE_MARGIN_PX` from the edge, at least
    `STAR_CLEARANCE_PX` from every given star, at least
    `FAINT_SOURCE_CLEARANCE_PX` from every faint source (see
    `faint_source_distance`), at least `APERTURE_SEPARATION_PX` from every
    kept position, and where `signed_aperture_sum` works. When a covered mask
    is given, a draw is also kept at least `UNCOVERED_CLEARANCE_PX` from every
    pixel the mask marks as not covered. No test looks at an aperture's sum,
    so the choice cannot favor quiet apertures.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    stars : `numpy.ndarray`
        Star centers, shape ``(n, 2)`` as (row, column).
    source_distance : `numpy.ndarray`, optional
        The stack's `faint_source_distance`, when the caller already has it.
        Found here when left out.
    covered_mask : `numpy.ndarray`, optional
        `True` where every exposure group of a group stack has data, on the
        same pixel grid as `plane`. Left out for a stack of one group.

    Returns
    -------
    positions : `list` [`tuple` [`float`, `float`]]
        The kept (row, column) positions, at most `NOISE_APERTURE_COUNT`.
    """
    star_tree = cKDTree(stars) if len(stars) else None
    if source_distance is None:
        source_distance = faint_source_distance(plane)
    # The distance of every pixel to the nearest pixel a group does not cover
    # (zero on the uncovered pixels themselves).
    uncovered_distance = None if covered_mask is None else distance_transform_edt(covered_mask)
    height, width = plane.shape
    generator = np.random.default_rng(APERTURE_SEED)
    positions: list[tuple[float, float]] = []
    for _ in range(40 * NOISE_APERTURE_COUNT):
        row = generator.uniform(EDGE_MARGIN_PX, height - EDGE_MARGIN_PX)
        column = generator.uniform(EDGE_MARGIN_PX, width - EDGE_MARGIN_PX)
        if star_tree is not None and star_tree.query([row, column])[0] <= STAR_CLEARANCE_PX:
            continue
        if source_distance[round(row), round(column)] <= FAINT_SOURCE_CLEARANCE_PX:
            continue
        if (
            uncovered_distance is not None
            and uncovered_distance[round(row), round(column)] <= UNCOVERED_CLEARANCE_PX
        ):
            continue
        if positions and np.min(np.hypot(*(np.array(positions) - (row, column)).T)) < APERTURE_SEPARATION_PX:
            continue
        if signed_aperture_sum(plane, column, row) is None:
            continue
        positions.append((row, column))
        if len(positions) >= NOISE_APERTURE_COUNT:
            break
    return positions


@dataclass(frozen=True)
class FaintSourceAnalysis:
    """What the faint-source mask found in one stack.

    Parameters
    ----------
    distance : `numpy.ndarray`
        For every pixel, the distance in pixels to the nearest flagged pixel.
        Infinite when nothing is flagged; zero everywhere when the
        stack holds no usable pixel.
    noise_scales : `tuple` [`float`, ...]
        The robust noise scale (1.4826 times the median absolute deviation over
        the pixels that hold data) of the smoothed, background-free image at
        each of `FAINT_SOURCE_SMOOTHING_SIGMAS_PX`, in the stack's own units.
    flagged_fraction : `float`
        The share of all pixels that were flagged.
    usable_fraction : `float`
        The share of all pixels that hold data (finite and not exactly zero).
    excluded_fraction : `float`
        The share of all pixels within `FAINT_SOURCE_CLEARANCE_PX` of a flagged
        pixel, where no aperture may be centered. This is the share that limits
        placement; it is larger than the flagged share.
    """

    distance: np.ndarray
    noise_scales: tuple[float, ...]
    flagged_fraction: float
    usable_fraction: float
    excluded_fraction: float = 0.0


def analyze_faint_sources(plane: np.ndarray) -> FaintSourceAnalysis:
    """Find the faint sources of a stack and say how the mask was scaled.

    The rule uses only the stack's pixels, never an aperture's sum. The
    stack's large-scale background is taken away (a Gaussian of
    `FAINT_SOURCE_HIGH_PASS_SIGMA_PX`), the result is smoothed at each of
    `FAINT_SOURCE_SMOOTHING_SIGMAS_PX`, and a pixel is flagged when any
    smoothed value lies more than `FAINT_SOURCE_THRESHOLD_SIGMAS` robust
    sigmas above zero. Empty edge pixels (exactly zero or not finite) are
    filled with the stack's median first, so the edge does not look like a
    source, and they are left out of each noise scale, so the filled edge does
    not pull the scale down. The working arrays are 32-bit and freed as soon
    as they are used, which keeps the peak memory near five copies of the
    image (see validation entry STK-36).

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.

    Returns
    -------
    analysis : `FaintSourceAnalysis`
        The distance map, the noise scales and the flagged share.
    """
    image = np.asarray(plane, dtype=np.float32)
    usable = np.isfinite(image) & (image != 0)
    usable_fraction = float(np.count_nonzero(usable)) / usable.size if usable.size else 0.0
    if not usable.any():
        return FaintSourceAnalysis(np.zeros(image.shape, dtype=np.float32), (), 0.0, 0.0, 1.0)
    filled = np.where(usable, image, np.float32(np.median(image[usable])))
    flat = filled - gaussian_filter(filled, FAINT_SOURCE_HIGH_PASS_SIGMA_PX)
    del filled
    flagged = np.zeros(image.shape, dtype=bool)
    scales: list[float] = []
    for width in FAINT_SOURCE_SMOOTHING_SIGMAS_PX:
        smooth = gaussian_filter(flat, width)
        values = smooth[usable]
        center = np.median(values)
        values -= center
        np.abs(values, out=values)
        sigma = MAD_TO_SIGMA * float(np.median(values))
        del values
        scales.append(sigma)
        if sigma > 0:
            flagged |= smooth > FAINT_SOURCE_THRESHOLD_SIGMAS * sigma
        del smooth
    del flat
    flagged_fraction = float(np.count_nonzero(flagged)) / flagged.size
    if not flagged.any():
        distance = np.full(image.shape, np.inf, dtype=np.float32)
    else:
        distance = distance_transform_edt(~flagged)
    excluded_fraction = float(np.count_nonzero(distance <= FAINT_SOURCE_CLEARANCE_PX)) / distance.size
    return FaintSourceAnalysis(distance, tuple(scales), flagged_fraction, usable_fraction, excluded_fraction)


def faint_source_distance(plane: np.ndarray) -> np.ndarray:
    """Give each pixel's distance to the nearest faint source in a stack.

    See `analyze_faint_sources` for the rule.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.

    Returns
    -------
    distance : `numpy.ndarray`
        For every pixel, the distance in pixels to the nearest flagged pixel.
        Infinite when nothing is flagged.
    """
    return analyze_faint_sources(plane).distance


def _isolated_stars(stars: np.ndarray, clearance: float = STAR_CLEARANCE_PX) -> np.ndarray:
    """Keep the stars that have no detected neighbor within the clearance.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star centers, shape ``(n, 2)`` as (row, column), brightest first.
    clearance : `float`, optional
        How far the nearest neighbor must be.

    Returns
    -------
    isolated : `numpy.ndarray`
        The stars whose nearest neighbor is farther than the clearance, in
        the same order.
    """
    if len(stars) < 2:
        return stars
    nearest = cKDTree(stars).query(stars, k=2)[0][:, 1]
    return stars[nearest > clearance]


def _read_aperture_file(path: str) -> dict[str, dict]:
    """Read the sets stored in a target's aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key. Empty when the file is
        missing, cannot be read, or has the layout of the first development
        version (those sets lack the reference brightnesses and are not
        used).
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != APERTURE_FILE_VERSION:
            logger.warning("The stored noise apertures at %s have an old layout and will be replaced.", path)
            return {}
        return dict(data["sets"])
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return {}


def load_apertures(path: str, setup_key: str | None = None) -> ApertureSet | None:
    """Read a target's stored apertures.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The camera, optic and reference exposure wanted. Left out, the set
        the library stack recorded (see `record_library_set_key`), or in an
        older file the deepest stored set (see `_deepest_key`). A set stored
        under a key with no exposure (`is_legacy_setup_key`) is never returned.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The stored set, or `None` when the file is missing or cannot be read
        (a warning says so in the second case), or holds no set for the key.
    """
    sets = _read_aperture_file(path)
    if setup_key is None:
        data = sets.get(_default_key(sets, _read_library_keys(path)) or "")
    elif is_legacy_setup_key(setup_key):
        data = None
    else:
        data = sets.get(setup_key)
    if data is None:
        return None
    try:
        return ApertureSet.from_json(data)
    except (ValueError, KeyError, TypeError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return None


def _deepest_key(sets: dict[str, dict], equipment: str | None = None) -> str | None:
    """Pick the deepest stored set, the fallback for the library stack's set.

    A subset stack or an odd–even difference job has to use the library stack's
    set, whatever exposure length its own reference frame has. The record
    written by `record_library_set_key` names that set. This depth rule is
    used only when there is no record (a file written before it existed):
    the library stack's set is then taken to be the one chosen on the deepest
    stack. It can be wrong, because a stale set from an old stack can be
    deeper than the current stack's.

    Parameters
    ----------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    equipment : `str`, optional
        Only keys of this camera and optic are considered. Left out, all.

    Returns
    -------
    setup_key : `str` or `None`
        The key of the set with the largest effective frame count (an unknown
        count counts as 0; the first stored wins a tie), or `None` when there
        is no usable key. Keys without an exposure are skipped.
    """
    best_key = None
    best_depth = -1.0
    for key, data in sets.items():
        if is_legacy_setup_key(key) or (equipment is not None and _equipment_of_key(key) != equipment):
            continue
        try:
            depth = float(data.get("defining_effective_frame_count") or 0.0)
        except AttributeError, TypeError, ValueError:
            depth = 0.0
        if depth > best_depth:
            best_key, best_depth = key, depth
    return best_key


def _default_key(sets: dict[str, dict], library_keys: dict[str, str]) -> str | None:
    """Pick the set to use when the caller names no camera or exposure.

    Parameters
    ----------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    library_keys : `dict` [`str`, `str`]
        The recorded library set key of each camera and optic.

    Returns
    -------
    setup_key : `str` or `None`
        The deepest of the recorded library sets that are still stored, or,
        when none is recorded, the deepest stored set (`_deepest_key`).
    """
    recorded = {key: sets[key] for key in library_keys.values() if key in sets}
    return _deepest_key(recorded or sets)


def find_library_set_key(path: str, setup_key: str) -> str | None:
    """Find the key of the library stack's set for a stack that may not choose.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`
        The key the stack itself would have (see `_setup_key`).

    Returns
    -------
    library_key : `str` or `None`
        The set the library stack recorded for the same camera and optic
        (`record_library_set_key`), whatever its exposure length, when it is
        still stored. Otherwise (a file written before the record existed)
        the deepest stored set of that camera and optic, or `None` when there
        is none. Depth alone can pick a stale set: an old stack of one
        exposure length can be deeper, by effective frame count, than the
        current stack of folded exposure groups, whose unequal weights lower
        its count.
    """
    sets = _read_aperture_file(path)
    equipment = _equipment_of_key(setup_key)
    recorded = _read_library_keys(path).get(equipment)
    if recorded is not None and recorded in sets and not is_legacy_setup_key(recorded):
        return recorded
    return _deepest_key(sets, equipment)


def _read_library_keys(path: str) -> dict[str, str]:
    """Read which set each camera and optic's library stack uses.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    library_keys : `dict` [`str`, `str`]
        The setup key of the library stack's set, by camera and optic (see
        `_equipment_of_key`). Empty when the file is missing, unreadable, of
        another version, or written before the record existed.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != APERTURE_FILE_VERSION:
            return {}
        return {
            str(equipment): str(key) for equipment, key in dict(data.get("library_set_keys") or {}).items()
        }
    except OSError, ValueError, AttributeError, TypeError:
        return {}


def record_library_set_key(path: str, setup_key: str) -> None:
    """Record that a library stack uses a set, so other stacks find that one.

    A library stack (one that may choose apertures) calls this after it has
    measured its noise in a set, whether it loaded the set or made it. The key
    is kept under the set's camera and optic and replaces the earlier record.
    Stale sets are not deleted. The file keeps the same version: an older
    reader ignores the extra mapping, and a file without it still loads.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`
        The key of the set the library stack used. Nothing is recorded when
        the file holds no set under that key.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        sets = _read_aperture_file(path)
        if setup_key not in sets or is_legacy_setup_key(setup_key):
            return
        library_keys = _read_library_keys(path)
        equipment = _equipment_of_key(setup_key)
        if library_keys.get(equipment) == setup_key:
            return
        library_keys[equipment] = setup_key
        _write_aperture_file(path, sets, library_keys)


def load_apertures_with_reason(path: str, setup_key: str | None = None) -> tuple[ApertureSet | None, str]:
    """Read a target's stored apertures, and say why not when there are none.

    A subset stack uses this: it never chooses apertures, so it must tell a
    missing file from a file of an older version.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The camera, optic and reference exposure wanted. Left out, the
        deepest stored set (see `load_apertures`).

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The stored set, or `None`.
    reason : `str`
        Why there is no set. Empty when there is one. A missing file, a file
        of an older version (`OLD_APERTURES_REASON`), a file with no set for
        this camera and optic, and an unreadable file each have their own
        sentence.
    """
    if not os.path.isfile(path):
        return None, (
            "the target has no stored noise apertures; stack the target once so its library stack "
            "chooses them"
        )
    if _stored_file_version(path) != APERTURE_FILE_VERSION:
        return None, OLD_APERTURES_REASON
    aperture_set = load_apertures(path, setup_key)
    if aperture_set is None:
        return None, (
            "the target's stored noise apertures hold no usable set for this camera, optic and "
            f"reference exposure ({setup_key}); stack the target once so its library stack chooses one"
        )
    return aperture_set, ""


def _stored_file_version(path: str) -> int | None:
    """Read the layout version written in an aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    version : `int` or `None`
        The version, or `None` when the file cannot be read or has none.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            version = json.load(handle).get("version")
        return int(version) if version is not None else None
    except OSError, ValueError, AttributeError, TypeError:
        return None


def _aperture_lock_path(path: str) -> str:
    """Name the lock file that guards one aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    lock_path : `str`
        A file in the locks folder, named after the aperture file's path.
    """
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return os.path.join(locks_directory(), f"quality_apertures_{digest}.lock")


def _write_aperture_file(
    path: str, sets: dict[str, dict], library_keys: dict[str, str] | None = None
) -> None:
    """Write the aperture file whole, in one step.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    library_keys : `dict` [`str`, `str`], optional
        The library stack's set key by camera and optic. Left out, the record
        already in the file is kept. Entries whose set is not in ``sets`` are
        dropped.
    """
    if library_keys is None:
        library_keys = _read_library_keys(path)
    library_keys = {equipment: key for equipment, key in library_keys.items() if key in sets}
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".quality_apertures_", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": APERTURE_FILE_VERSION, "sets": sets, "library_set_keys": library_keys}, handle
            )
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _store_apertures_once(
    path: str, aperture_set: ApertureSet, replace_identity: str | None = None
) -> ApertureSet:
    """Save the set unless another run saved one for the same equipment first.

    The file is read, changed and replaced while holding a lock, so two
    stacks of one target that finish together cannot overwrite each other's
    apertures; the loser uses the winner's.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    aperture_set : `ApertureSet`
        The set to save.
    replace_identity : `str`, optional
        The identity of the stored set this one replaces (a deeper stack chose
        a new set). The stored set is replaced only if it still has this
        identity; if another run replaced it first, that run's set is kept.

    Returns
    -------
    stored : `ApertureSet`
        The set now in the file for this equipment: this one, or the other
        run's.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        sets = _read_aperture_file(path)
        existing = sets.get(aperture_set.setup_key)
        if existing is not None:
            try:
                stored = ApertureSet.from_json(existing)
                if replace_identity is None or stored.identity != replace_identity:
                    return stored
            except ValueError, KeyError, TypeError:
                logger.warning("A stored set of noise apertures at %s was unreadable and is replaced.", path)
        # A set stored under a key with no exposure (an older file) is never
        # used again, so the new set for the same equipment takes its place.
        equipment = _equipment_of_key(aperture_set.setup_key)
        for key in [key for key in sets if is_legacy_setup_key(key) and _equipment_of_key(key) == equipment]:
            del sets[key]
        sets[aperture_set.setup_key] = aperture_set.to_json()
        _write_aperture_file(path, sets)
    return aperture_set


def reset_apertures(path: str, setup_key: str | None = None) -> bool:
    """Forget a target's stored apertures, so the next stack chooses new ones.

    Use this when the stored stars or patches are no longer good (a nebula
    grew, a star was misjudged) or the equipment's key changed by accident.
    Noise values measured before the reset and after it are not comparable.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The one camera, optic and reference exposure to forget. Left out,
        every set is forgotten
        and the file is deleted.

    Returns
    -------
    removed : `bool`
        `True` when something was removed.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        if setup_key is None:
            if not os.path.isfile(path):
                return False
            os.unlink(path)
            return True
        sets = _read_aperture_file(path)
        if setup_key not in sets:
            return False
        del sets[setup_key]
        if sets:
            _write_aperture_file(path, sets)
        else:
            os.unlink(path)
        return True


def _is_much_deeper(depth: StackDepth | None, aperture_set: ApertureSet) -> bool:
    """Say whether a stack is deep enough to replace a stored set of apertures.

    Parameters
    ----------
    depth : `StackDepth` or `None`
        The stack's frame counts.
    aperture_set : `ApertureSet`
        The stored set.

    Returns
    -------
    deeper : `bool`
        True when both effective frame counts are known and the stack's is at
        least `REPLACEMENT_DEPTH_FACTOR` times the stored set's.
    """
    defining = aperture_set.defining_effective_frame_count
    if depth is None or defining is None or defining <= 0:
        return False
    return depth.effective_frame_count >= REPLACEMENT_DEPTH_FACTOR * defining
