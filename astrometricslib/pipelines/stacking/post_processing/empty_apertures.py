"""The fixed empty apertures of a target's noise: a set and its name.

To compare the noise of two stacks of one target, every stack must be measured
in the same patches of sky. This module holds the description of those patches
(it does not measure any noise):

* `ApertureSet` is one chosen set, `StackDepth` holds the frame counts of the
  stack that chose it, and a set is replaced when a library stack has at least
  `REPLACEMENT_DEPTH_FACTOR` times the effective frame count
  (`is_much_deeper`).
* `setup_key` names the camera, optic and reference exposure length a set
  belongs to. A stack made with other equipment, or lined up on a frame of
  another exposure length, starts a new set. Noise values measured with
  different sets are not comparable.
* `aperture_identity_to_number` and `aperture_identity_from_number` turn an
  aperture's sky position into a stable number and back.

The set is chosen in `aperture_placement.py` and kept in
`aperture_storage.py`. It imports `stack_image` only; `stack_noise.py` and
`difference_noise.py` import it. The estimator and the numbers are declared in
`pipelines/stacking/metrics.py`.
"""

import hashlib
import math
from dataclasses import dataclass

import numpy as np
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    APERTURE_RADIUS_PX,
    RING_INNER_PX,
    RING_OUTER_PX,
)

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


# The ``wcs_source`` of a stack whose solution came from plate-solving the
# stack itself. Only such a solution is on the stack's own pixel grid, so only
# a set chosen with it has true sky positions (`ApertureSet`). Measured on a
# copy of the library (2026-10-27): 9 of 11 sets chosen with the registration
# reference frame's header had positions 11 to 50 arcsec off the Gaia stars;
# the two chosen by plate solve (M 13, M 101) were 0.1 to 0.9 arcsec off.
PLATE_SOLVE_WCS_SOURCE = "plate solve of the stack"

# The two ways a stack's own plate solve is now recorded. Both start with
# `PLATE_SOLVE_WCS_SOURCE`, so older files and `is_plate_solve_wcs_source`
# still recognize them. The refit reads cached Gaia stars only.
PLATE_SOLVE_REFIT_WCS_SOURCE = f"{PLATE_SOLVE_WCS_SOURCE}, refit to cached Gaia"
PLATE_SOLVE_UNREFINED_WCS_SOURCE = f"{PLATE_SOLVE_WCS_SOURCE}, not refit"


def is_plate_solve_wcs_source(wcs_source: str) -> bool:
    """Say whether a stored ``wcs_source`` names a plate solve of the stack.

    Parameters
    ----------
    wcs_source : `str`
        The source stored with an aperture set or a noise row.

    Returns
    -------
    is_plate_solve : `bool`
        True for `PLATE_SOLVE_WCS_SOURCE` (written by older code, after a
        refit to Gaia) and for the refit and not-refit values that begin
        with it.
    """
    return str(wcs_source).startswith(PLATE_SOLVE_WCS_SOURCE)


# The version of the file's layout. Version 3 holds one set of apertures for
# each camera, optic and reference exposure, placed away from faint sources
# as well as bright
# stars (see `FAINT_SOURCE_THRESHOLD_SIGMAS`), with the depth of the stack that
# defined it. Version 2 apertures were placed away from the 400 brightest stars
# only; faint stars sat in them. Version 1 held one set, with no per-star
# reference brightness. Older files are replaced when a library stack is
# measured, never used, and a subset stack refuses them with a reason.
APERTURE_FILE_VERSION = 3

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

REFERENCE_RING_GAP_PX = RING_INNER_PX - APERTURE_RADIUS_PX

REFERENCE_RING_WIDTH_PX = RING_OUTER_PX - RING_INNER_PX


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
        ``"stack header"``, ``"registration reference frame"``, a member frame
        or a plate solve of the stack (`PLATE_SOLVE_REFIT_WCS_SOURCE` or
        `PLATE_SOLVE_UNREFINED_WCS_SOURCE`; older files hold
        `PLATE_SOLVE_WCS_SOURCE`).
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
        set belongs to (see `setup_key`).
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
    sky_positions_from_plate_solve : `bool`
        True when the stored right ascensions and declinations came from a
        plate solve of the stack that chose the set, so they are true sky
        positions. A header or frame solution can be tens of arcseconds off
        the stack's pixel grid; such a set still works for the noise (each
        later stack fits the stored stars to its own), but its positions must
        not be matched to Gaia. A file written before this field existed is
        trusted only when its ``wcs_source`` is a plate solve
        (`is_plate_solve_wcs_source`).
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
    sky_positions_from_plate_solve: bool = False

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
            "sky_positions_from_plate_solve": self.sky_positions_from_plate_solve,
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
            sky_positions_from_plate_solve=bool(
                data.get("sky_positions_from_plate_solve", is_plate_solve_wcs_source(str(data["wcs_source"])))
            ),
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


def setup_key(*headers: fits.Header, reference_exposure_seconds: float | None = None) -> str:
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


def equipment_of_key(setup_key: str) -> str:
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


def is_much_deeper(depth: StackDepth | None, aperture_set: ApertureSet) -> bool:
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


__all__ = [
    "PLATE_SOLVE_REFIT_WCS_SOURCE",
    "PLATE_SOLVE_UNREFINED_WCS_SOURCE",
    "PLATE_SOLVE_WCS_SOURCE",
    "ApertureSet",
    "ApertureSetMismatchError",
    "StackDepth",
    "aperture_identity_from_number",
    "aperture_identity_to_number",
    "is_legacy_setup_key",
    "is_much_deeper",
    "is_plate_solve_wcs_source",
    "setup_key",
]
