"""Purpose: Test the go-to scatter of the simulated mount.

Description: Plays a made-up INDI server (two callables) against
`simulator.goto_scatter.GotoScatter`: a slew to a new place draws a new
offset, a sync and a re-slew to the same place do not, the same seed gives the
same offsets, and the offsets spread as the settings say. Needs no INDI
server.
"""

import math
import statistics

import pytest

from simulator.fit.fitting import GO_TO_SCATTER_SEED, MOUNT_MODEL_SIGNS
from simulator.goto_scatter import (
    CLEAR_SYNCS_SETTING,
    COMMIT_SYNCS_SETTING,
    STATE_NAME,
    TARGET_DEC_NAME,
    TARGET_RA_NAME,
    GotoScatter,
    GotoScatterSettings,
    mount_model_terms,
)

SETTINGS = GotoScatterSettings(
    mean_ra_arcsec=100.0, mean_dec_arcsec=-50.0, sigma_ra_arcsec=600.0, sigma_dec_arcsec=500.0, count=51
)


class FakeServer:
    """A made-up INDI server: a mount state and target, and a log of writes."""

    def __init__(self) -> None:
        """Start idle, with no writes."""
        self.state = "Ok"
        self.target = (0.0, 0.0)
        self.writes: list[str] = []

    def read(self, name: str) -> str:
        """Give the value of one property element.

        Returns
        -------
        value : `str`
            The value, or an empty text.
        """
        return {
            STATE_NAME: self.state,
            TARGET_RA_NAME: str(self.target[0]),
            TARGET_DEC_NAME: str(self.target[1]),
        }.get(name, "")

    def write(self, *settings: str) -> None:
        """Remember what was set."""
        self.writes.extend(settings)

    def slew(self, ra_hours: float, dec_degrees: float) -> None:
        """Start a slew to a place."""
        self.target = (ra_hours, dec_degrees)
        self.state = "Busy"

    def land(self) -> None:
        """End the slew."""
        self.state = "Ok"


def _scatter(server: FakeServer, settings: GotoScatterSettings = SETTINGS) -> GotoScatter:
    """Make a scatter on a made-up server.

    Returns
    -------
    scatter : `GotoScatter`
        The scatter.
    """
    return GotoScatter(settings, server.read, server.write)


def test_a_slew_to_a_new_place_clears_the_syncs_and_writes_an_offset() -> None:
    """The first slew draws, clears the learned syncs and writes both terms."""
    server = FakeServer()
    scatter = _scatter(server)
    server.slew(5.0, 20.0)

    assert scatter.check() is True

    assert server.writes[:2] == [CLEAR_SYNCS_SETTING, COMMIT_SYNCS_SETTING]
    assert server.writes[2].startswith("Telescope Simulator.MOUNT_MODEL.MM_IH;MM_ID=")
    ra_offset, dec_offset = scatter.draws[0]
    ih, id_ = mount_model_terms(ra_offset, dec_offset)
    assert server.writes[2].endswith(f"={ih};{id_}")
    assert server.writes[3] == "Telescope Simulator.EQUATORIAL_EOD_COORD.RA;DEC=5.0;20.0"
    assert ih == pytest.approx(MOUNT_MODEL_SIGNS["MM_IH"] * ra_offset / 60.0)
    assert id_ == pytest.approx(MOUNT_MODEL_SIGNS["MM_ID"] * dec_offset / 60.0)


def test_an_idle_mount_draws_nothing() -> None:
    """No slew, no draw."""
    server = FakeServer()
    scatter = _scatter(server)

    assert scatter.check() is False
    assert server.writes == []


def test_a_re_slew_to_the_same_place_keeps_the_offset() -> None:
    """After a sync the mount slews back to the same place: no new draw."""
    server = FakeServer()
    scatter = _scatter(server)
    server.slew(5.0, 20.0)
    assert scatter.check() is True
    server.land()
    # Centering syncs (the state stays Ok) and slews to the same place again.
    server.slew(5.0, 20.0)

    assert scatter.check() is False
    assert len(scatter.draws) == 1


def test_a_slew_to_another_place_draws_again() -> None:
    """A place a degree away is a new destination."""
    server = FakeServer()
    scatter = _scatter(server)
    server.slew(5.0, 20.0)
    scatter.check()
    server.land()
    server.slew(6.0, 25.0)

    assert scatter.check() is True
    assert len(scatter.draws) == 2
    assert scatter.draws[0] != scatter.draws[1]


def test_the_same_seed_gives_the_same_offsets() -> None:
    """Two runs with one seed draw the same offsets; another seed does not."""
    first = _scatter(FakeServer())
    second = _scatter(FakeServer())
    other = _scatter(
        FakeServer(),
        GotoScatterSettings(100.0, -50.0, 600.0, 500.0, seed=GO_TO_SCATTER_SEED + 1),
    )

    assert [first.draw() for _ in range(5)] == [second.draw() for _ in range(5)]
    assert first.draw() != other.draw()


def test_the_offsets_scatter_as_the_settings_say() -> None:
    """Many draws have the settings' middle and spread on each axis."""
    scatter = _scatter(FakeServer())
    draws = [scatter.draw() for _ in range(4000)]
    ra = [draw[0] for draw in draws]
    dec = [draw[1] for draw in draws]

    assert statistics.mean(ra) == pytest.approx(100.0, abs=40.0)
    assert statistics.mean(dec) == pytest.approx(-50.0, abs=40.0)
    assert statistics.pstdev(ra) == pytest.approx(600.0, rel=0.05)
    assert statistics.pstdev(dec) == pytest.approx(500.0, rel=0.05)
    # The errors point every which way, not along one line.
    angles = [math.atan2(y, x) for x, y in draws]
    assert max(angles) - min(angles) > math.pi


def test_a_huge_offset_is_clamped_to_the_property_range() -> None:
    """The terms never go beyond 120 arcminutes."""
    ih, id_ = mount_model_terms(-100_000.0, 100_000.0)

    assert ih == pytest.approx(120.0)
    assert id_ == pytest.approx(120.0)


def test_settings_are_read_from_the_results_file_entry() -> None:
    """A good entry gives settings; a missing or bad one gives none."""
    entry = {
        "mean_ra_arcsec": 1.0,
        "mean_dec_arcsec": 2.0,
        "sigma_ra_arcsec": 3.0,
        "sigma_dec_arcsec": 4.0,
        "seed": 7,
        "count": 9,
    }

    assert GotoScatterSettings.from_dict(entry) == GotoScatterSettings(1.0, 2.0, 3.0, 4.0, 7, 9)
    assert GotoScatterSettings.from_dict(None) is None
    assert GotoScatterSettings.from_dict({"mean_ra_arcsec": "x"}) is None
