"""Bundled constellation stick-figure line data.

Constellation line topology (which stars are traditionally connected to draw
each figure) is fixed, cultural/artistic data — not a live astronomical
measurement — so unlike the star catalog drivers, this is a small static
bundled file loaded once, not something queried by region or magnitude.

Line data is derived from d3-celestial (https://github.com/ofrohn/d3-celestial),
Copyright (c) 2015 Olaf Frohn, BSD-3-Clause license. Each line segment's
RA/Dec endpoints are already resolved in the source data, so no star-catalog
lookup is needed here.

REQ: PLN-3.3
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONSTELLATION_LINES_FILENAME = "constellation_lines.json"


def default_constellation_lines_path() -> Path:
    """Return the default on-disk location for the bundled line data.

    The data is the constellation stick-figure line-segment dataset.

    Returns
    -------
    path : `pathlib.Path`
        The default constellation line data file path.
    """
    return Path(__file__).parent.parent / "drivers" / "catalog" / CONSTELLATION_LINES_FILENAME


class ConstellationLineLibrary:
    """Serves bundled constellation stick-figure line segments.

    Lazily loads and caches the full ~89-constellation, ~750-segment dataset
    from a static JSON file. There is no spatial or magnitude filtering —
    the whole dataset is small enough to return in one shot and let the
    frontend project/cull it client-side against the current viewport.

    REQ: PLN-3.3
    """

    def __init__(self, data_path: Path | None = None):  # ruff: ignore[missing-return-type-special-method]
        self._data_path = data_path or default_constellation_lines_path()
        self._cached_segments: list[dict[str, Any]] | None = None

    def get_all_line_segments(self) -> list[dict[str, Any]]:
        """Return every line segment across all bundled constellations.

        Returns
        -------
        List[Dict[str, Any]]
            One entry per line segment:
            {"constellation": "Ori", "ra1": .., "dec1": .., "ra2": ..,
            "dec2": ..}
        """
        if self._cached_segments is not None:
            return self._cached_segments

        if not self._data_path.exists():
            logger.warning("Constellation line data not found at %s", self._data_path)
            self._cached_segments = []
            return self._cached_segments

        with open(self._data_path) as data_file:
            raw_data = json.load(data_file)

        segments: list[dict[str, Any]] = []
        for constellation in raw_data.get("constellations", []):
            abbreviation = constellation["abbreviation"]
            for ra1, dec1, ra2, dec2 in constellation["segments"]:
                segments.append({
                    "constellation": abbreviation,
                    "ra1": ra1,
                    "dec1": dec1,
                    "ra2": ra2,
                    "dec2": dec2,
                })

        self._cached_segments = segments
        return self._cached_segments


def get_constellation_line_segments(sky) -> list[dict[str, Any]]:  # ruff: ignore[missing-type-function-argument]
    """Return all bundled constellation stick-figure line segments.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the constellation line library.

    Returns
    -------
    List[Dict[str, Any]]
        One entry per line segment: {"constellation", "ra1", "dec1",
        "ra2", "dec2"}.

    REQ: PLN-3.3
    """
    return sky._constellation_lines.get_all_line_segments()
