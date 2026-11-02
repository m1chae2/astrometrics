"""Tests that dot chaining gives the same chains as the plain all-pairs search.

Chaining used to compare every open chain with every dot of each new picture
in a Python loop. It now finds the nearby pairs with a tree search. These
tests keep the answer the same: one small input with a fixed expected
result, and random inputs checked against a copy of the plain search. The
inputs include dots next to RA = 0 and chains near both celestial poles.
"""

import math

import numpy as np
import pytest

from astrometricslib.models.moving_object import FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.detection import MovingObjectDetector
from astrometricslib.pipelines.shared.angles import wrapped_ra_difference_deg

_SECONDS_PER_HOUR = 3600.0
_FIRST_TIMESTAMP = 1.7e9


def _dot(
    frame_index: int, dot_id: int, ra_deg: float, dec_deg: float, gap_seconds: float = 120.0
) -> FrameDetection:
    """Build one fake dot.

    The `dot_id` is kept in `pixel_x` so chains can be compared.

    Parameters
    ----------
    frame_index : `int`
        Which picture the dot is in. Pictures are `gap_seconds` apart.
    dot_id : `int`
        A number that is unique over the whole input.
    ra_deg, dec_deg : `float`
        Sky position in degrees.
    gap_seconds : `float`
        Time between pictures.

    Returns
    -------
    detection : `FrameDetection`
        The fake dot.
    """
    return FrameDetection(
        frame_path=f"frame_{frame_index:03d}",
        timestamp=_FIRST_TIMESTAMP + frame_index * gap_seconds,
        pixel_x=float(dot_id),
        pixel_y=float(frame_index),
        right_ascension_deg=ra_deg % 360.0,
        declination_deg=dec_deg,
    )


def _plain_all_pairs_chaining(
    config: MovingObjectConfig, detections: list[FrameDetection]
) -> list[list[FrameDetection]]:
    """Chain dots by comparing every open chain with every dot (old method).

    Parameters
    ----------
    config : `MovingObjectConfig`
        Supplies the rate limit and the radius cap.
    detections : `list` [`FrameDetection`]
        Dots of one night.

    Returns
    -------
    chains : `list` [`list` [`FrameDetection`]]
        The chains, in the order they were opened.
    """
    by_frame: dict[str, list[FrameDetection]] = {}
    for detection in detections:
        by_frame.setdefault(detection.frame_path, []).append(detection)
    open_chains: list[list[FrameDetection]] = []
    for frame_path in sorted(by_frame, key=lambda path: by_frame[path][0].timestamp):
        dots = list(by_frame[frame_path])
        if not open_chains:
            open_chains.extend([dot] for dot in dots)
            continue
        ra_arr = np.array([d.right_ascension_deg for d in dots])
        dec_arr = np.array([d.declination_deg for d in dots])
        matches = []
        for chain_index, chain in enumerate(open_chains):
            last = chain[-1]
            hours = abs(dots[0].timestamp - last.timestamp) / _SECONDS_PER_HOUR
            radius = min(config.rate_max_arcsec_per_hour * hours, config.chain_match_radius_max_arcsec)
            cos_dec = math.cos(math.radians(last.declination_deg))
            max_deg = (radius / 3600.0) * 1.2
            half_width = max_deg / max(abs(cos_dec), 1e-6)
            ra_diff_all = wrapped_ra_difference_deg(ra_arr, last.right_ascension_deg)
            mask = (
                (np.abs(ra_diff_all) <= half_width)
                & (dec_arr >= last.declination_deg - max_deg)
                & (dec_arr <= last.declination_deg + max_deg)
            )
            indices = np.where(mask)[0]
            if indices.size == 0:
                continue
            distances = np.hypot(
                ra_diff_all[indices] * cos_dec * 3600.0,
                (dec_arr[indices] - last.declination_deg) * 3600.0,
            )
            for index, distance in zip(
                indices[distances <= radius], distances[distances <= radius], strict=True
            ):
                matches.append((float(distance), chain_index, int(index)))
        matches.sort(key=lambda item: item[0])
        taken = np.zeros(len(dots), dtype=bool)
        used: set[int] = set()
        for _distance, chain_index, index in matches:
            if taken[index] or chain_index in used:
                continue
            open_chains[chain_index].append(dots[index])
            taken[index] = True
            used.add(chain_index)
        open_chains.extend([dots[i]] for i in np.where(~taken)[0])
    return open_chains


def _ids(chains: list[list[FrameDetection]]) -> list[list[int]]:
    """Reduce chains to dot numbers.

    Parameters
    ----------
    chains : `list` [`list` [`FrameDetection`]]
        Chains to reduce.

    Returns
    -------
    ids : `list` [`list` [`int`]]
        The `dot_id` of each dot, chain by chain.
    """
    return [[int(dot.pixel_x) for dot in chain] for chain in chains]


def test_fixed_small_input_gives_expected_chains() -> None:
    """Check the chains of one small input against fixed numbers.

    Dots 0-3 are a mover at 100 arcsec per hour. Dot ids 10-13 are a star
    across RA = 0 that dithers over the 0/360 line. Dots 20 and 21 start two
    chains that both reach the single dot 22 in frame 1, which must go to the
    nearer one. Dot 30 is a lone dot. Dots 40-42 sit in a picture 20 minutes
    after the rest, to check chains that skip a picture.
    """
    gap = 120.0
    per_degree = 3600.0
    dots = [
        _dot(0, 0, 150.0, 30.0, gap),
        _dot(1, 1, 150.0 + 3.0 / per_degree, 30.0, gap),
        _dot(2, 2, 150.0 + 6.0 / per_degree, 30.0, gap),
        _dot(3, 3, 150.0 + 9.0 / per_degree, 30.0, gap),
        _dot(0, 10, 359.9999, 10.0, gap),
        _dot(1, 11, 0.0002, 10.0, gap),
        _dot(2, 12, 359.9998, 10.0, gap),
        _dot(3, 13, 0.0001, 10.0, gap),
        _dot(0, 20, 50.0, -20.0, gap),
        _dot(0, 21, 50.0 + 1.0 / per_degree, -20.0, gap),
        _dot(1, 22, 50.0 + 0.7 / per_degree, -20.0, gap),
        _dot(2, 30, 200.0, 5.0, gap),
        _dot(10, 40, 150.0 + 12.0 / per_degree, 30.0, gap),
    ]
    chains = MovingObjectDetector(MovingObjectConfig())._chain_detections_by_persistence(dots)
    assert _ids(chains) == _ids(_plain_all_pairs_chaining(MovingObjectConfig(), dots))
    assert _ids(chains) == [[0, 1, 2, 3, 40], [10, 11, 12, 13], [20], [21, 22], [30]]


@pytest.mark.parametrize(
    ("centre_ra_deg", "centre_dec_deg", "seed"),
    [
        (150.0, 30.0, 1),
        (359.95, 10.0, 2),
        (0.05, -10.0, 3),
        (40.0, 88.5, 4),
        (200.0, -89.7, 5),
        (10.0, 78.0, 6),
    ],
)
def test_random_input_matches_plain_all_pairs_search(
    centre_ra_deg: float, centre_dec_deg: float, seed: int
) -> None:
    """Random input chains exactly as the plain all-pairs search does.

    The stars have noise and a few of them move.

    The field is a few arcminutes wide so that many chains compete for the
    same dots, which is where the claiming order matters.
    """
    generator = np.random.default_rng(seed)
    star_count, frame_count = 60, 8
    field_deg = 0.06
    stars = generator.uniform(-field_deg, field_deg, size=(star_count, 2))
    dots = []
    dot_id = 0
    for frame_index in range(frame_count):
        for star_index in range(star_count):
            ra_offset, dec_offset = stars[star_index] + generator.normal(0, 3.0 / 3600.0, 2)
            moving = star_index < 4
            if moving:
                ra_offset += frame_index * 0.0008
            dots.append(
                _dot(
                    frame_index,
                    dot_id,
                    centre_ra_deg + ra_offset / max(math.cos(math.radians(centre_dec_deg)), 1e-3),
                    centre_dec_deg + dec_offset,
                )
            )
            dot_id += 1
        for _ in range(8):
            ra_offset, dec_offset = generator.uniform(-field_deg, field_deg, 2)
            dots.append(
                _dot(
                    frame_index,
                    dot_id,
                    centre_ra_deg + ra_offset / max(math.cos(math.radians(centre_dec_deg)), 1e-3),
                    centre_dec_deg + dec_offset,
                )
            )
            dot_id += 1
    config = MovingObjectConfig()
    chains = MovingObjectDetector(config)._chain_detections_by_persistence(dots)
    assert _ids(chains) == _ids(_plain_all_pairs_chaining(config, dots))
