"""Keeps a preview stretch's black point below the empty sky.

The preview stretch puts its black point a little below the sky level, so the
sky lands on a chosen gray and faint light above it shows. The level comes
from `measure_sky_background`, which clips out what stands above the sky. When
a nebula fills the frame (NGC 2244, the Rosette), no part of the frame is
empty and that level is nebula light. The black point then lands above the
darkest, outermost sky, and the stretch clips that sky to pure black. A later
denoising step turns the black into gray, which draws a dark ring round the
nebula.

This module adds a guard that checks the result on the pixels. It counts the
share of the picture's pixels that fall below the black point. If the share is
more than `CLIPPED_SHARE_LIMIT`, the black point is lowered until only that
share is clipped. A frame whose sky was measured well is not touched.

The noise and the level are still measured by `measure_sky_background`. A
level read from a low percentile of 64 by 64 bin medians was tried instead
and moved the sky of clean fields by up to 0.02 in display units (STK-D78),
so the guard alone is used.

The numbers are written to the preview settings record (`StretchSky.record`).

`sky_background_for_file` measures a stack's file for the stretch, and
`choose_sky_stretch` turns the measurement into the stretch the Siril driver
writes (``siril.preview.stretch.SkyStretch``).
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib._internal.drivers.siril.preview.stretch import SkyStretch
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.image_scaling import (
    AUTOSTRETCH_SHADOWS_CLIP_SIGMA,
    measure_sky_background,
    midtones_balance_for,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CLIPPED_SHARE_LIMIT",
    "StretchSky",
    "choose_sky_stretch",
    "measure_stretch_sky",
    "sky_background_for_file",
]

# The largest share of the picture's pixels the stretch may clip to black.
# Pure sky noise cut at the stretch's -2.8 sigma clip loses 0.26% of its
# pixels (a Gaussian tail). On the six stacks of STK-D78 (2026-11-23) the
# stretch clipped 0.18% to 0.28% on M 52, M 101 and M 13, 0.53% on M 42,
# 3.3% on M 31 and 16.7% on NGC 2244. 0.5% is about twice the noise floor, so a
# well-measured sky never trips it, and it is far below the two stacks whose
# outer sky was clipped.
CLIPPED_SHARE_LIMIT = TunedParameter(
    name="stacking.stretch_clipped_share_limit",
    default=0.005,
    unit="fraction",
    description="Largest share of the picture's pixels the stretch may clip to black.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D78",
)

# At most this many pixels are checked for the clipped share. A regular
# stride over the picture gives the same share within a few parts in
# ten thousand and keeps the check to a fraction of a second on a 24
# megapixel stack.
_CLIP_CHECK_SAMPLE_COUNT = 2_000_000


@dataclass(frozen=True)
class StretchSky:
    """The empty sky's measurement for a stretch, and what was checked.

    Attributes
    ----------
    level : `float`
        The sky level, in the image's own units, that the stretch should put
        at the chosen display level. When the guard lowered the black point,
        this is the level that gives that black point.
    noise : `float`
        The sky noise (one standard deviation), in the same units.
    black_point : `float`
        The black point the stretch will use.
    clipped_share : `float`
        The share of valid pixels below the final black point.
    first_clipped_share : `float`
        The same share before the guard acted.
    guard_lowered : `bool`
        Whether the guard lowered the black point.
    whole_frame_level : `float`
        The level the old measure gave (the frame's clipped median).
    """

    level: float
    noise: float
    black_point: float
    clipped_share: float
    first_clipped_share: float
    guard_lowered: bool
    whole_frame_level: float

    def as_background(self) -> tuple[float, float]:
        """Give the measurement in the form the stretch command takes.

        Returns
        -------
        sky_background : `tuple` [`float`, `float`]
            The sky level and noise.
        """
        return self.level, self.noise

    def record(self) -> dict[str, Any]:
        """Give the measurement for the preview settings record.

        Returns
        -------
        record : `dict`
            Plain numbers and flags for the JSON file.
        """
        return {
            "black_point": self.black_point,
            "clipped_share": self.clipped_share,
            "first_clipped_share": self.first_clipped_share,
            "guard_lowered_black_point": self.guard_lowered,
            "sky_level": self.level,
            "sky_noise": self.noise,
            "whole_frame_sky_level": self.whole_frame_level,
        }


def _clipped_share(valid_values: np.ndarray, black_point: float) -> float:
    """Give the share of pixels below a black point.

    Parameters
    ----------
    valid_values : `numpy.ndarray`
        The picture's pixel values, 1-D, without empty border pixels.
    black_point : `float`
        The black point.

    Returns
    -------
    share : `float`
        The share of values below it, 0 for an empty array.
    """
    if valid_values.size == 0:
        return 0.0
    return float(np.count_nonzero(valid_values < black_point)) / valid_values.size


def measure_stretch_sky(data: np.ndarray) -> StretchSky | None:
    """Measure the sky for a stretch, and check that it clips little.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image the stretch is applied to, in linear units: two-dimensional,
        or color planes in front (the planes are averaged, as one shared
        stretch treats them).

    Returns
    -------
    sky : `StretchSky` or `None`
        The measurement. `None` when the sky cannot be measured, so the
        caller keeps the stretch it had.
    """
    array = np.squeeze(np.asarray(data))
    if array.ndim == 3 and array.shape[0] <= 4:
        image = np.mean(array, axis=0, dtype=np.float64)
    elif array.ndim == 2:
        image = array
    else:
        return None
    sky = measure_sky_background(image)
    if sky is None:
        return None
    level, noise = sky
    whole_frame_level = level
    clip_sigma = AUTOSTRETCH_SHADOWS_CLIP_SIGMA
    black_point = max(level + clip_sigma * noise, 0.0)

    stride = max(1, int(np.ceil(image.size / _CLIP_CHECK_SAMPLE_COUNT)))
    sample = image.ravel()[::stride]
    valid = sample[np.isfinite(sample) & (sample > 0.0)]
    limit = float(resolve(CLIPPED_SHARE_LIMIT).value)
    first_share = _clipped_share(valid, black_point)
    lowered = first_share > limit
    if lowered:
        black_point = min(black_point, float(np.quantile(valid, limit)))
        level = black_point - clip_sigma * noise
        logger.info(
            "The stretch would clip %.1f%% of the picture to black; its black point is lowered to %.6g.",
            100.0 * first_share,
            black_point,
        )
    return StretchSky(
        level=level,
        noise=noise,
        black_point=black_point,
        clipped_share=_clipped_share(valid, black_point),
        first_clipped_share=first_share,
        guard_lowered=lowered,
        whole_frame_level=whole_frame_level,
    )


def choose_sky_stretch(
    sky_level: float, linked: bool = False, sky_background: tuple[float, float] | None = None
) -> SkyStretch:
    """Choose the stretch that puts a stack's sky on a brightness.

    Siril's own ``autostretch`` takes the median and spread of every pixel as
    the sky. When a large galaxy covers much of the frame, those are galaxy
    light and the sky lands darker than asked. When the empty sky has been
    measured (`sky_background`), the same stretch is set from that
    measurement instead: a black point and a midtones balance.

    Parameters
    ----------
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on.
    linked : `bool`, optional
        Whether all colour channels share one stretch. By default Siril
        stretches each channel on its own, which sets every channel's sky and
        brightness scale to the same values. That undoes any colour balance
        made on the linear data, so a picture whose colours were calibrated
        needs the shared stretch. Default `False`.
    sky_background : `tuple` [`float`, `float`], optional
        The empty sky's level and noise in the image's own units, from
        `sky_background_for_file`. Only for a single-channel image. If `None`
        (the default), Siril's ``autostretch`` measures the sky itself.

    Returns
    -------
    stretch : `SkyStretch`
        The stretch, with the black point and midtones balance when the
        measured sky gives a usable curve.
    """
    if sky_background is not None:
        level, noise = sky_background
        black_point = max(level + AUTOSTRETCH_SHADOWS_CLIP_SIGMA * noise, 0.0)
        normalized_sky = (level - black_point) / (1.0 - black_point)
        if 0.0 < normalized_sky < 1.0 and 0.0 < sky_level < 1.0:
            midtones = midtones_balance_for(normalized_sky, sky_level)
            return SkyStretch(sky_level, AUTOSTRETCH_SHADOWS_CLIP_SIGMA, linked, black_point, midtones)
    return SkyStretch(sky_level, AUTOSTRETCH_SHADOWS_CLIP_SIGMA, linked)


def sky_background_for_file(
    path: str,
    data_edited_first: bool = False,
    linked: bool = False,
    record: dict[str, Any] | None = None,
) -> tuple[float, float] | None:
    """Measure the empty sky of a stack's linear image for the stretch.

    The measurement is `measure_stretch_sky`: the library's sky level and
    noise, with a guard that lowers the black point when the stretch would
    clip more than a small share of the picture to black (a nebula that fills
    the frame lifts the measured level above the real sky).

    Parameters
    ----------
    path : `str`
        The FITS image the stretch will be applied to.
    data_edited_first : `bool`, optional
        Whether a Siril step that changes the pixel values (Remove Green
        Noise) runs before the stretch, so a measurement made here would not
        match what the stretch sees. Default `False`.
    linked : `bool`, optional
        Whether a colour image gets one stretch shared by its channels. Only
        then can one sky measurement (of the channels' mean) stand for all of
        them. Default `False`.
    record : `dict`, optional
        If given, it is filled with the black point and the clipped share
        (see `StretchSky.record`), for the preview settings record.

    Returns
    -------
    sky_background : `tuple` [`float`, `float`] or `None`
        The sky level and noise, or `None` when Siril should measure the sky
        itself: a colour image whose channels are stretched one by one, a
        step that edits the data first, a file that cannot be read, or a sky
        that cannot be measured.
    """
    if data_edited_first:
        return None
    try:
        data = np.asarray(AstrometricsImage(path).data)
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read '%s' to measure its sky: %s", path, error)
        return None
    if np.squeeze(data).ndim == 3 and not linked:
        return None
    sky = measure_stretch_sky(data)
    if sky is None:
        return None
    if record is not None:
        record.update(sky.record())
    return sky.as_background()
