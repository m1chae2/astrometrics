"""Purpose: Tests for naming the detected stars of a stack.

Description: A stack's detections are matched to the Gaia stars of its cache
tiles and, when online, to SIMBAD, whose most familiar name wins. Unmatched
detections are "Unnamed star", and bright Gaia stars with no detection are
listed too. These tests use a small made-up stack and stand-ins for the
detections, the tiles and SIMBAD, so no network is needed.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.detections import DetectionsMeasurement, StarDetection
from astrometricslib.pipelines.astrometry.processing import stack_identification as module
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import FieldGaiaStars
from astrometricslib.pipelines.shared.catalog_star_identity import SimbadStarNames

SIZE = 200


def _wcs() -> WCS:
    """Build a north-up solution of a 200-pixel stack at 2 arcsec per pixel.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The solution.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [100.0, 30.0]
    wcs.wcs.crpix = [SIZE / 2.0, SIZE / 2.0]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    return wcs


def _stack(tmp_path: Path, frames: int = 20, solved: bool = True) -> str:
    """Write a small stack with a plate solution.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test folder.
    frames : `int`, optional
        The ``STACKCNT`` card.
    solved : `bool`, optional
        Mark the solution as a real plate solution.

    Returns
    -------
    path : `str`
        The file.
    """
    header = _wcs().to_header()
    header["STACKCNT"] = frames
    header["DATE-OBS"] = "2026-10-04T07:36:15"
    if solved:
        header["PLTSOLVD"] = True
    path = tmp_path / "Test_L_Stacked.fits"
    fits.PrimaryHDU(np.zeros((SIZE, SIZE), dtype=np.float32), header=header).writeto(path)
    return str(path)


def _sky(x: float, y: float) -> tuple[float, float]:
    """Turn a 0-based pixel into a sky position under the test solution.

    Parameters
    ----------
    x, y : `float`
        The pixel.

    Returns
    -------
    ra, dec : `float`
        Degrees.
    """
    ra, dec = _wcs().all_pix2world([x], [y], 0)
    return float(ra[0]), float(dec[0])


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    simbad: tuple[Any, list[SimbadStarNames], str] | None = None,
) -> None:
    """Stand in for the detections, the Gaia tiles and SIMBAD.

    Three detections at pixels (50, 50), (100, 100) and (150, 150). Gaia has
    stars at the first two and one bright star at (30, 170) with no
    detection.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the steps.
    path : `str`
        The stack.
    simbad : `tuple`, optional
        What the SIMBAD step returns; by default an offline note.
    """
    detections = [
        StarDetection(x=x + 1.0, y=x + 1.0, flux=1000.0 / (index + 1))
        for index, x in enumerate((50, 100, 150))
    ]
    monkeypatch.setattr(
        module,
        "measure_detections_in_file",
        lambda _path: DetectionsMeasurement(
            path=path,
            width=SIZE,
            height=SIZE,
            detections=detections,
            detection_count=3,
            merged_count=0,
            kernel_fwhm_px=3.0,
        ),
    )
    positions = [_sky(50, 50), _sky(100, 100), _sky(30, 170)]
    table = Table({
        "source_id": ["11", "22", "33"],
        "ra": [p[0] for p in positions],
        "dec": [p[1] for p in positions],
        "phot_g_mean_mag": [12.0, 14.0, 10.0],
        "DESIGNATION": ["Gaia DR3 11", "Gaia DR3 22", "Gaia DR3 33"],
        "pmra": [0.0, 0.0, 0.0],
        "pmdec": [0.0, 0.0, 0.0],
    })
    coords = SkyCoord(ra=table["ra"] * u.deg, dec=table["dec"] * u.deg)
    monkeypatch.setattr(
        module,
        "load_field_gaia_stars",
        lambda config, tiles, depth, fetch, **_k: FieldGaiaStars(
            table=table, coords=coords, depth=depth, tiles=[1], complete_tiles=[1]
        ),
    )
    monkeypatch.setattr(module, "make_gaia_tile_fetcher", lambda: (None, "offline test"))
    monkeypatch.setattr(module, "tiles_under_image", lambda *_a, **_k: [1])
    monkeypatch.setattr(
        module,
        "_simbad_names",
        lambda *_a: simbad or (None, [], "Offline: the names come from Gaia only."),
    )
    monkeypatch.setattr(module, "_record_simbad_aliases", lambda *_a: 0)


def test_offline_every_detection_gets_a_gaia_name_or_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gaia names two detections, the third is unnamed, a missed star shows."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)

    result = module.identify_stack_stars(path, config=None)

    names = [star.name for star in result.stars]
    kinds = [star.kind for star in result.stars]
    assert names[:3] == ["Gaia DR3 11", "Gaia DR3 22", module.UNNAMED_STAR]
    assert kinds == ["matched", "matched", "unmatched", "catalog_only"]
    assert result.stars[3].gaia_source_id == "33"
    assert [star.library_id for star in result.stars] == ["Gaia DR3 11", "Gaia DR3 22", None, "Gaia DR3 33"]
    assert result.solution_reused
    assert any("Gaia only" in note for note in result.notes)
    assert (result.matched_count, result.unmatched_count, result.catalog_only_count) == (2, 1, 1)


def test_a_simbad_name_wins_over_the_gaia_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SIMBAD's familiar name replaces the Gaia number of the same star."""
    path = _stack(tmp_path)
    ra, dec = _sky(50, 50)
    _setup(
        monkeypatch,
        path,
        (
            SkyCoord([ra] * u.deg, [dec] * u.deg),
            [SimbadStarNames("Gaia DR3 11", "Vega", 11, ["* alf Lyr", "HD 172167"])],
            "",
        ),
    )

    result = module.identify_stack_stars(path, config=None)

    assert result.stars[0].name == "Vega"
    assert result.stars[0].name_source == "simbad"
    assert result.stars[0].aliases == ["* alf Lyr", "HD 172167"]
    assert result.stars[0].library_id == "Gaia DR3 11"
    assert result.simbad_used
    assert result.notes == []


def test_the_result_is_saved_and_read_back_until_the_stack_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The saved names are read until the stack changes."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)
    result = module.identify_stack_stars(path, config=None)

    assert module.read_stack_identification(path) == result
    with fits.open(path, mode="update", memmap=False) as hdul:
        hdul[0].header["NEWCARD"] = 1
    assert module.read_stack_identification(path) is None


def test_a_single_frame_is_refused(tmp_path: Path) -> None:
    """Raw frames never get this treatment."""
    path = _stack(tmp_path, frames=1)

    with pytest.raises(InvalidArgumentError, match="stacks only"):
        module.identify_stack_stars(path, config=None)


def test_a_stop_request_stops_the_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop asked for before the matching ends the call with an error."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)

    with pytest.raises(module.StackIdentificationStoppedError):
        module.identify_stack_stars(path, config=None, should_stop=lambda: True)
    assert module.read_stack_identification(path) is None


class _FakeSimbadIdentifier:
    """Stand in for the star identifier, answering a SIMBAD table."""

    def __init__(self, rows: list[tuple[str, float]]) -> None:
        """Remember the rows.

        Parameters
        ----------
        rows : `list` [`tuple` [`str`, `float`]]
            The ``|``-separated identifiers and the declination of each row.
        """
        self.rows = rows

    def _query_simbad_region(self, *_args: Any, **_kwargs: Any) -> tuple[Table, SkyCoord]:
        """Give the table and the positions of the rows.

        Returns
        -------
        table, coords : `Table`, `SkyCoord`
            The rows, all at one right ascension.
        """
        table = Table({"ids": [ids for ids, _ in self.rows]})
        return table, SkyCoord(ra=[10.0] * len(self.rows) * u.deg, dec=[dec for _, dec in self.rows] * u.deg)


def _simbad_answer(rows: list[tuple[str, float]]) -> tuple[Any, ...]:
    """Run the SIMBAD step on made-up rows.

    Parameters
    ----------
    rows : `list` [`tuple` [`str`, `float`]]
        The identifiers and declination of each row.

    Returns
    -------
    answer : `tuple`
        What `_simbad_names` returns.
    """
    return module._simbad_names(_FakeSimbadIdentifier(rows), _wcs(), SIZE, SIZE)


def test_one_name_per_star_whatever_the_order_of_simbad_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two rows of one Gaia star give one star, in either row order."""
    monkeypatch.setattr(module, "network_is_offline", lambda: False)
    rows = [
        ("TYC 2743-1974-1|Gaia DR3 1903910765541999872", 34.4100),
        ("HD 214181|HIP 111500|Gaia DR2 1903910765541999872|Gaia DR3 1903910765541999872", 34.4101),
    ]
    first = _simbad_answer(rows)
    second = _simbad_answer(rows[::-1])

    assert first[1] == second[1]
    names = first[1][0]
    assert (names.name, names.record_id) == ("HD 214181", "Gaia DR3 1903910765541999872")
    assert names.gaia_source_id == 1903910765541999872
    assert names.aliases == ["HIP 111500"]


def test_equal_names_of_one_kind_are_picked_alphabetically(monkeypatch: pytest.MonkeyPatch) -> None:
    """Of two Henry Draper numbers, the same one is chosen always."""
    monkeypatch.setattr(module, "network_is_offline", lambda: False)
    one = _simbad_answer([("HD 2|HD 1|Gaia DR3 5", 34.0)])
    other = _simbad_answer([("HD 1|HD 2|Gaia DR3 5", 34.0)])

    assert one[1][0].name == other[1][0].name == "HD 1"


class _FakeCatalogAccess:
    """A star catalog in memory that follows the real merge rule."""

    def __init__(self, records: list[Any]) -> None:
        """Hold the records.

        Parameters
        ----------
        records : `list` [`StellarObject`]
            The saved stars.
        """
        self.records = {record.id: record for record in records}

    def get_by_ids(self, _dataset: str, ids: list[str]) -> list[Any]:
        """Give the saved records among the ids.

        Parameters
        ----------
        _dataset : `str`
            Ignored.
        ids : `list` [`str`]
            The wanted ids.

        Returns
        -------
        records : `list` [`StellarObject`]
            The ones that exist.
        """
        return [self.records[star_id].model_copy(deep=True) for star_id in ids if star_id in self.records]

    def merge_and_record(self, _dataset: str, objects: list[Any], merge: Any) -> None:
        """Save each object through the merge rule.

        Parameters
        ----------
        _dataset : `str`
            Ignored.
        objects : `list` [`StellarObject`]
            The records to merge in.
        merge : `Callable`
            The merge rule.
        """
        for record in objects:
            self.records[record.id] = merge(self.records.get(record.id), record)


def test_simbad_names_are_added_to_saved_records_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing aliases are added to a saved record and nothing else."""
    from astrometricslib._internal.drivers import catalog_access as catalog_module
    from astrometricslib.models.stack_identification import IdentifiedStar
    from astrometricslib.models.stellar_source import StellarObject

    saved = StellarObject(id="Gaia DR3 11", name="Gaia DR3 11", aliases=["HIP 5"], magnitude=7.5)
    fake = _FakeCatalogAccess([saved])
    monkeypatch.setattr(catalog_module, "CatalogAccess", lambda _config: fake)

    def star(gaia: str, name: str, aliases: list[str]) -> IdentifiedStar:
        """Make a matched star.

        Parameters
        ----------
        gaia : `str`
            Its Gaia number.
        name, aliases : `str`, `list` [`str`]
            Its SIMBAD name and aliases.

        Returns
        -------
        star : `IdentifiedStar`
            The star.
        """
        return IdentifiedStar(
            id=f"detection-{gaia}", name=name, kind="matched", x=0.0, y=0.0, ra_deg=0.0, dec_deg=0.0,
            gaia_source_id=gaia, name_source="simbad", aliases=aliases,
        )  # fmt: skip

    stars = [
        star("11", "HD 214181", ["HIP 5", "TYC 2743-1974-1"]),
        star("22", "HD 9", ["TYC 1-1-1"]),  # no saved record: stays uncreated
        star("33", "HD 7", ["TYC 7-7-7"]),  # Gaia number SIMBAD does not list: not trusted
    ]
    listed = [SimbadStarNames(f"Gaia DR3 {gaia}", "", int(gaia), []) for gaia in ("11", "22")]
    updated = module._record_simbad_aliases(None, stars, listed)

    assert updated == 1
    record = fake.records["Gaia DR3 11"]
    assert record.aliases == ["HIP 5", "HD 214181", "TYC 2743-1974-1"]
    assert (record.id, record.name, record.magnitude) == ("Gaia DR3 11", "Gaia DR3 11", 7.5)
    assert set(fake.records) == {"Gaia DR3 11"}
    # A second run finds nothing to add.
    assert module._record_simbad_aliases(None, stars, listed) == 0
    assert fake.records["Gaia DR3 11"].aliases == ["HIP 5", "HD 214181", "TYC 2743-1974-1"]


def test_a_file_saved_before_library_ids_gets_them_when_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old file's stars get a library id from a Gaia number or name."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)
    result = module.identify_stack_stars(path, config=None)
    for star in result.stars:
        star.library_id = None
    result.stars[2].name_source, result.stars[2].name, result.stars[2].kind = "simbad", "HD 5", "matched"
    module._save(path, result)

    read = module.read_stack_identification(path)

    assert [star.library_id for star in read.stars] == ["Gaia DR3 11", "Gaia DR3 22", "HD 5", "Gaia DR3 33"]
