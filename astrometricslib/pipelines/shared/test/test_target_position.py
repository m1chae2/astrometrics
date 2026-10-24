"""Tests for giving a target the position of its object.

The SIMBAD driver is faked, so no test uses the network. They check that the
catalog answer wins over a frame center, that a frame center is a marked
fallback, and that a position already set is not changed.
"""

from types import SimpleNamespace
from typing import Any

import numpy as np
from astropy.table import Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.astrometry import runner
from astrometricslib.pipelines.shared import target_position
from astrometricslib.pipelines.shared.target_position import (
    FILL_CATALOG,
    FILL_FRAME_CENTER,
    FILL_NONE,
    fill_target_position,
    parse_target_position,
)

# Ring Nebula (M 57) in decimal degrees, and a frame center 38 arcmin away.
RING_RA_DEG = 283.396
RING_DEC_DEG = 33.029
OFFSET_FRAME_CENTER = target_position.SkyCoord(ra=283.98, dec=32.63, unit="deg")


class FakeSimbad:
    """A SIMBAD stand-in that answers from a dictionary."""

    def __init__(self, answers: dict[str, tuple[float, float]], fail: bool = False) -> None:
        """Remember the known names and whether every lookup should fail."""
        self.answers = answers
        self.fail = fail
        self.asked: list[str] = []

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Return a one-row table for a known name, else `None`.

        Parameters
        ----------
        object_name : `str`
            The name asked about.
        votable_fields : `tuple` [`str`], optional
            Ignored.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            The answer.

        Raises
        ------
        ExternalServiceError
            When this fake was built to fail.
        """
        self.asked.append(object_name)
        if self.fail:
            raise ExternalServiceError("down")
        if object_name not in self.answers:
            return None
        ra, dec = self.answers[object_name]
        return Table({"ra": np.array([ra]), "dec": np.array([dec])})


def test_catalog_position_beats_frame_center() -> None:
    """A resolvable name gets its catalog position, not the frame center."""
    target = Target(id="M 57")
    action = fill_target_position(
        target, FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)}), OFFSET_FRAME_CENTER
    )
    assert action == FILL_CATALOG
    assert not target.position_unverified
    position = parse_target_position(target)
    assert position is not None
    assert (
        position.separation(target_position.SkyCoord(ra=RING_RA_DEG, dec=RING_DEC_DEG, unit="deg")).arcsec < 1
    )


def test_unresolved_name_falls_back_and_is_marked() -> None:
    """An unknown name takes the frame center and is marked unverified."""
    target = Target(id="Mystery")
    assert fill_target_position(target, FakeSimbad({}), OFFSET_FRAME_CENTER) == FILL_FRAME_CENTER
    assert target.position_unverified
    assert parse_target_position(target) is not None


def test_lookup_failure_falls_back() -> None:
    """A SIMBAD outage is treated as an unresolved name."""
    target = Target(id="M 57")
    assert fill_target_position(target, FakeSimbad({}, fail=True), OFFSET_FRAME_CENTER) == FILL_FRAME_CENTER
    assert target.position_unverified


def test_no_catalog_and_no_frame_leaves_target_empty() -> None:
    """No answer and no frame center leaves the target empty."""
    target = Target(id="Mystery")
    assert fill_target_position(target, FakeSimbad({}), None) == FILL_NONE
    assert parse_target_position(target) is None


def test_existing_position_is_never_overwritten() -> None:
    """A set, verified position is left alone; SIMBAD is not asked."""
    target = Target(id="M 57", ra="18 53 35.08", dec="+33 01 45.03")
    simbad = FakeSimbad({"M 57": (1.0, 2.0)})
    assert fill_target_position(target, simbad, OFFSET_FRAME_CENTER) == FILL_NONE
    assert target.ra == "18 53 35.08"
    assert simbad.asked == []


def test_unverified_position_is_replaced_only_by_a_catalog_answer() -> None:
    """An unverified position is upgraded by a catalog answer only."""
    target = Target(id="M 57", ra="18 55 54.17", dec="+32 37 45.85", position_unverified=True)
    assert fill_target_position(target, FakeSimbad({}), OFFSET_FRAME_CENTER) == FILL_NONE
    assert target.ra == "18 55 54.17"
    assert fill_target_position(target, FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)})) == FILL_CATALOG
    assert not target.position_unverified


def test_runner_fill_uses_catalog_not_wcs_center() -> None:
    """The runner's fill uses SIMBAD even when a solved WCS is offered."""
    wcs = SimpleNamespace(wcs=SimpleNamespace(crval=[283.98, 32.63]))
    target = Target(id="M 57")
    drivers = SimpleNamespace(simbad_or_default=lambda: FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)}))
    runner._fill_target_position(target, SimpleNamespace(wcs=wcs), drivers)
    position = parse_target_position(target)
    assert position is not None
    assert abs(position.dec.deg - RING_DEC_DEG) < 0.001
    assert not target.position_unverified
