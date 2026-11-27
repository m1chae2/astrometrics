"""Tests for giving a target the position of its object.

The SIMBAD driver is faked, so no test uses the network. They check that the
catalog answer wins over a frame center, that a frame center is a marked
fallback, and that a position already set is not changed.
"""

import logging
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.astrometry import runner
from astrometricslib.pipelines.shared import target_position
from astrometricslib.pipelines.shared.target_position import (
    FILL_CATALOG,
    FILL_FRAME_CENTER,
    FILL_NONE,
    FILL_TYPE_ONLY,
    fill_target_position,
    parse_target_position,
)

# Ring Nebula (M 57) in decimal degrees, and a frame center 38 arcmin away.
RING_RA_DEG = 283.396
RING_DEC_DEG = 33.029
OFFSET_FRAME_CENTER = target_position.SkyCoord(ra=283.98, dec=32.63, unit="deg")


class FakeSimbad:
    """A SIMBAD stand-in that answers from a dictionary."""

    def __init__(
        self,
        answers: dict[str, tuple[float, float]],
        fail: bool = False,
        error: Exception | None = None,
        object_type: str | None = None,
    ) -> None:
        """Remember the known names, the object type and how lookups fail."""
        self.object_type = object_type
        self.answers = answers
        self.fail = fail
        self.error = error
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
            When this fake was built to fail, or the error it was given.
        """
        self.asked.append(object_name)
        if self.error is not None:
            raise self.error
        if self.fail:
            raise ExternalServiceError("down")
        if object_name not in self.answers:
            return None
        ra, dec = self.answers[object_name]
        table = Table({"ra": np.array([ra]), "dec": np.array([dec])})
        if self.object_type is not None:
            table["otype"] = [self.object_type]
        return table


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
    target = Target(id="M 57", ra="18 53 35.08", dec="+33 01 45.03", simbad_object_type="PN")
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


@pytest.mark.parametrize("target_id", ["Mars", "Moon", "C 2023 A3 Tsuchinshan", "flat"])
def test_moving_and_calibration_targets_get_no_position(target_id: str) -> None:
    """Moving and calibration targets are never looked up or filled."""
    target = Target(id=target_id)
    simbad = FakeSimbad({target_id: (RING_RA_DEG, RING_DEC_DEG)})
    assert fill_target_position(target, simbad, OFFSET_FRAME_CENTER) == FILL_NONE
    assert simbad.asked == []
    assert parse_target_position(target) is None
    drivers = SimpleNamespace(simbad_or_default=lambda: simbad)
    runner._fill_target_position(target, SimpleNamespace(wcs=None), drivers)
    assert simbad.asked == []
    assert parse_target_position(target) is None


def test_network_error_never_fails_the_run() -> None:
    """An OSError is logged; the frame center becomes the fallback."""
    wcs = SimpleNamespace(wcs=SimpleNamespace(crval=[283.98, 32.63]), pixel_shape=None)
    target = Target(id="M 57")
    drivers = SimpleNamespace(simbad_or_default=lambda: FakeSimbad({}, error=OSError("no network")))
    runner._fill_target_position(target, SimpleNamespace(wcs=wcs), drivers)
    assert target.position_unverified
    assert parse_target_position(target) is not None


def test_catalog_position_outside_the_frame_is_distrusted(caplog: pytest.LogCaptureFixture) -> None:
    """A catalog answer far outside the frame keeps the frame center."""
    target = Target(id="M 57")
    simbad = FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)})
    with caplog.at_level(logging.WARNING):
        action = fill_target_position(target, simbad, OFFSET_FRAME_CENTER, footprint_radius_arcmin=5.0)
    assert action == FILL_FRAME_CENTER
    assert target.position_unverified
    assert "outside the frame" in caplog.text
    inside = Target(id="M 57")
    assert (
        fill_target_position(inside, simbad, OFFSET_FRAME_CENTER, footprint_radius_arcmin=60.0)
        == FILL_CATALOG
    )


def test_id_and_common_name_that_disagree_are_not_trusted() -> None:
    """The M 52 / Bubble Nebula case: neither place is accepted."""
    target = Target(id="M 52", common_name="Bubble Nebula")
    simbad = FakeSimbad({"M 52": (351.2, 61.6), "Bubble Nebula": (350.2, 61.2)})
    assert fill_target_position(target, simbad, OFFSET_FRAME_CENTER) == FILL_FRAME_CENTER
    assert target.position_unverified
    assert simbad.asked == ["M 52", "Bubble Nebula"]
    assert fill_target_position(Target(id="M 52", common_name="Bubble Nebula"), simbad) == FILL_NONE


def test_id_and_common_name_that_agree_take_the_id() -> None:
    """Two names for one object resolve to the id's position."""
    target = Target(id="M 57", common_name="Ring Nebula")
    simbad = FakeSimbad({
        "M 57": (RING_RA_DEG, RING_DEC_DEG),
        "Ring Nebula": (RING_RA_DEG + 0.001, RING_DEC_DEG),
    })
    assert fill_target_position(target, simbad) == FILL_CATALOG
    assert not target.position_unverified


def test_hand_entered_position_clears_the_unverified_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ra or dec by hand clears the unverified flag."""
    from astrometricslib.pipelines.shared import target_records

    target = Target(id="M 57", ra="18 55 54.17", dec="+32 37 45.85", position_unverified=True)
    monkeypatch.setattr(target_records, "get_target", lambda _api, _target_id: target)
    monkeypatch.setattr(target_records, "save_targets", lambda _api: None)
    target_records.update_target(SimpleNamespace(), target, {"ra": "18 53 35.08"})
    assert not target.position_unverified


CONFIRMED_RA = "18 53 35.08"
CONFIRMED_DEC = "+33 01 45.03"


def test_missing_type_is_filled_and_position_kept() -> None:
    """A confirmed position with no type gets only the type from SIMBAD."""
    target = Target(id="M 57", ra=CONFIRMED_RA, dec=CONFIRMED_DEC)
    simbad = FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)}, object_type="PN")
    assert fill_target_position(target, simbad, OFFSET_FRAME_CENTER) == FILL_TYPE_ONLY
    assert target.simbad_object_type == "PN"
    assert (target.ra, target.dec) == (CONFIRMED_RA, CONFIRMED_DEC)
    assert not target.position_unverified


def test_present_type_is_not_looked_up() -> None:
    """A target that already has a type does not query SIMBAD."""
    target = Target(id="M 57", ra=CONFIRMED_RA, dec=CONFIRMED_DEC, simbad_object_type="PN")
    simbad = FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)}, object_type="G")
    assert fill_target_position(target, simbad, None) == FILL_NONE
    assert simbad.asked == []
    assert target.simbad_object_type == "PN"


def test_type_from_catalog_outside_the_frame_is_not_taken(caplog: pytest.LogCaptureFixture) -> None:
    """A catalog position far outside the solved frame gives no type."""
    target = Target(id="M 57", ra=CONFIRMED_RA, dec=CONFIRMED_DEC)
    simbad = FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)}, object_type="PN")
    with caplog.at_level(logging.WARNING):
        action = fill_target_position(target, simbad, OFFSET_FRAME_CENTER, footprint_radius_arcmin=5.0)
    assert action == FILL_NONE
    assert target.simbad_object_type is None
    assert "Not taking its object type" in caplog.text


def test_type_lookup_failure_does_not_fail_astrometry() -> None:
    """A SIMBAD outage while filling the type leaves the target unchanged."""
    target = Target(id="M 57", ra=CONFIRMED_RA, dec=CONFIRMED_DEC)
    assert fill_target_position(target, FakeSimbad({}, fail=True), None) == FILL_NONE
    drivers = SimpleNamespace(simbad_or_default=lambda: FakeSimbad({}, error=OSError("no network")))
    runner._fill_target_position(target, SimpleNamespace(wcs=None), drivers)
    assert target.simbad_object_type is None
    assert (target.ra, target.dec) == (CONFIRMED_RA, CONFIRMED_DEC)


def test_unresolvable_name_leaves_type_empty() -> None:
    """A name SIMBAD cannot resolve changes nothing."""
    target = Target(id="Mystery", ra=CONFIRMED_RA, dec=CONFIRMED_DEC)
    assert fill_target_position(target, FakeSimbad({}), None) == FILL_NONE
    assert target.simbad_object_type is None


def test_runner_fills_missing_type_of_confirmed_target() -> None:
    """The runner fills the type of a target with a confirmed position."""
    target = Target(id="M 57", ra=CONFIRMED_RA, dec=CONFIRMED_DEC)
    simbad = FakeSimbad({"M 57": (RING_RA_DEG, RING_DEC_DEG)}, object_type="PN")
    runner._fill_target_position(
        target, SimpleNamespace(wcs=None), SimpleNamespace(simbad_or_default=lambda: simbad)
    )
    assert target.simbad_object_type == "PN"


class FakeTapSimbad(FakeSimbad):
    """A SIMBAD stand-in that also answers one ADQL question about names."""

    def __init__(self) -> None:
        """Remember how many ADQL questions were asked."""
        super().__init__({})
        self.queries: list[str] = []

    def query_tap(self, adql_query: str) -> Table:
        """Answer for the two names the test asks about.

        Parameters
        ----------
        adql_query : `str`
            The ADQL question.

        Returns
        -------
        table : `astropy.table.Table`
            A row for "M  57" and one for "NAME Ring Nebula".
        """
        self.queries.append(adql_query)
        return Table({
            "queried_id": ["M  57", "NAME Ring Nebula"],
            "ra": [RING_RA_DEG, RING_RA_DEG + 0.001],
            "dec": [RING_DEC_DEG, RING_DEC_DEG],
            "otype": ["PN", "PN"],
            "galdim_majaxis": [1.4, 1.4],
        })


def test_two_names_are_looked_up_with_one_request() -> None:
    """The id and the common name of a target cost one SIMBAD request."""
    simbad = FakeTapSimbad()

    resolution = target_position.resolve_names(["M 57", "Ring Nebula"], simbad)

    assert resolution.position is not None
    assert len(simbad.queries) == 1
    assert simbad.asked == []  # no lookup by name was needed
    assert resolution.position.object_type == "PN"
    assert resolution.position.major_axis_arcmin == pytest.approx(1.4)


def test_a_name_missing_from_the_one_request_is_asked_by_itself() -> None:
    """A name the identifier table lacks still gets its own lookup."""
    simbad = FakeTapSimbad()
    simbad.answers = {"Messier 57": (RING_RA_DEG, RING_DEC_DEG)}

    target_position.resolve_names(["M 57", "Messier 57"], simbad)

    assert simbad.asked == ["Messier 57"]
