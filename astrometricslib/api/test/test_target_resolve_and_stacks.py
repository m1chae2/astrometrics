"""Purpose: Test `TargetCatalog.resolve` and the stacks kept by configuration.

Description: `resolve` looks a name up in the library's targets, then its
stars, then SIMBAD, and says which one knew it. A stack records the
observatory configuration its frames share, and
`find_configuration_stack` finds a target's stack of one configuration,
matching an older record by camera and focal length.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from astropy.table import Table

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.observatory_configuration import Camera, ObservatoryConfiguration, Telescope
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, StackConfigurationResult, Target
from astrometricslib.pipelines.shared import target_records
from astrometricslib.pipelines.shared.analysis_configuration import find_configuration_stack
from astrometricslib.pipelines.stacking import stage as stacking_stage


class _Simbad:
    """A SIMBAD stand-in that knows one name."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: list[str] = []

    def query_object(self, name: str, votable_fields: tuple[str, ...] = ()) -> Table | None:
        """Answer for ``M 57`` only.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            One row for ``M 57``, else `None`.
        """
        self.calls.append(name)
        if name != "M 57":
            return None
        return Table({"ra": [283.396], "dec": [33.029], "otype": ["PN"]})


def _catalog(targets: list[Target], stars: list[StellarObject], simbad: _Simbad) -> TargetCatalog:
    """Build a catalog over a fake storage and SIMBAD.

    Returns
    -------
    catalog : `TargetCatalog`
        The catalog.
    """
    storage = MagicMock()
    storage.get.return_value = list(targets)
    storage.find_star_ids_by_name.side_effect = lambda name: [star.id for star in stars if star.name == name]
    storage.get_by_ids.side_effect = lambda _table, ids: [star for star in stars if star.id in ids]
    return TargetCatalog(MagicMock(), storage, drivers=Drivers(simbad=simbad))


def test_a_library_target_is_found_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """A target with a position answers without asking SIMBAD."""
    target = Target(id="M 13", common_name="Hercules Cluster", ra="16 41 41.2", dec="36 27 37")
    simbad = _Simbad()
    catalog = _catalog([target], [], simbad)
    monkeypatch.setattr(target_records, "list_targets", lambda api: [target])

    found = catalog.resolve("hercules cluster")

    assert found is not None and found.source == "target" and found.target is target
    assert found.ra_deg == pytest.approx(250.42, abs=0.01)
    assert simbad.calls == []


def test_a_library_star_comes_next(monkeypatch: pytest.MonkeyPatch) -> None:
    """A star with a position answers when no target has the name."""
    star = StellarObject(id="Vega", name="Vega", ra=279.23, dec=38.78)
    catalog = _catalog([], [star], _Simbad())
    monkeypatch.setattr(target_records, "list_targets", lambda api: [])

    found = catalog.resolve("Vega")

    assert found is not None and found.source == "star" and found.star is star
    assert (found.ra_deg, found.dec_deg) == pytest.approx((279.23, 38.78))


def test_simbad_answers_last_and_an_unknown_name_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIMBAD answers a name the library lacks; an unknown name gives None."""
    catalog = _catalog([], [], _Simbad())
    monkeypatch.setattr(target_records, "list_targets", lambda api: [])

    found = catalog.resolve("M 57")

    assert found is not None and found.source == "simbad" and found.object_type == "PN"
    assert catalog.resolve("No Such Object") is None
    with pytest.raises(InvalidArgumentError):
        catalog.resolve(" ")


def _configuration(configuration_id: str, camera: str, focal_length_mm: float) -> ObservatoryConfiguration:
    """Make a configuration of one camera behind one telescope.

    Returns
    -------
    configuration : `ObservatoryConfiguration`
        The configuration.
    """
    return ObservatoryConfiguration(
        id=configuration_id,
        telescope=Telescope(id="scope", name="Scope", focal_length_mm=focal_length_mm),
        camera=Camera(
            id="camera", name=camera, pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008
        ),
    )


def test_a_stack_is_found_by_its_configuration_or_by_its_equipment() -> None:
    """A recorded id wins; an older record matches by its equipment."""
    target = Target(id="M 31")
    target.stacking.stacks_by_configuration = {
        "Mono@400mm": StackConfigurationResult(
            configuration_key="Mono@400mm", configuration_id="c1", camera="Mono", stacked_image="/a.fits"
        ),
        "Color@300mm": StackConfigurationResult(
            configuration_key="Color@300mm", camera="Color", focal_length_mm=300.0, stacked_image="/b.fits"
        ),
    }

    assert find_configuration_stack(target, _configuration("c1", "Mono", 400.0)).stacked_image == "/a.fits"
    assert find_configuration_stack(target, _configuration("c2", "Color", 300.4)).stacked_image == "/b.fits"
    assert find_configuration_stack(target, _configuration("c3", "Color", 500.0)) is None
    assert find_configuration_stack(target) is None


def test_a_new_stack_records_the_configuration_its_frames_share(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage keeps the frames' shared configuration id on the record."""
    monkeypatch.setattr(
        "astrometricslib.foundation.config.get_configuration",
        lambda: SimpleNamespace(
            get_primary_camera_name=lambda: None, get_primary_focal_length_mm=lambda: None
        ),
    )
    target = Target(id="M 31")
    frames = [FrameRecord(path=f"/f{index}.fits", camera="Mono") for index in range(2)]
    for frame in frames:
        frame.focal_length_mm = 400.0
        frame.configuration_id = "c1"

    stacking_stage._record_configuration_stack(target, frames, "/stack.fits")

    (record,) = target.stacking.stacks_by_configuration.values()
    assert record.configuration_id == "c1"
