"""Tests for the target-size backfill's decision for one target.

A size is taken from the row of the target's first known name nearest its
stored position; an answer too far away, a missing size or an unknown name
gives no size.
"""

from types import SimpleNamespace

from astrometricslib.pipelines.shared.simbad_object_types import normalize_name
from astrometricslib.scripts.backfill_target_sizes import SizeOutcome, decide_size


def _target(ra: str = "18h53m35s", dec: str = "+33d01m45s") -> SimpleNamespace:
    """Build a target with a verified stored position.

    Returns
    -------
    target : `SimpleNamespace`
        The stand-in target.
    """
    return SimpleNamespace(id="M 57", common_name="", ra=ra, dec=dec, position_unverified=False)


def test_a_size_near_the_stored_position_is_taken() -> None:
    """The row at the stored position gives its size."""
    rows = {normalize_name("M 57"): [{"ra": 283.396, "dec": 33.029, "major_axis": 1.4}]}
    assert decide_size(_target(), rows) == (SizeOutcome.FILLED, 1.4)


def test_no_size_or_an_unknown_name_gives_none() -> None:
    """A masked or missing size, or a name SIMBAD does not know, is no size."""
    rows = {normalize_name("M 57"): [{"ra": 283.396, "dec": 33.029, "major_axis": None}]}
    assert decide_size(_target(), rows)[0] is SizeOutcome.NO_SIZE
    assert decide_size(_target(), {})[0] is SizeOutcome.NOT_IN_SIMBAD
