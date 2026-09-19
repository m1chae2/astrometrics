"""Purpose: Unit tests for the CatalogAccess layer and its integration.

Description: Verifies that CatalogAccess resolves paths and catalogs
correctly, and that a mock catalog_access can be injected to isolate scientific
core logic.
"""

from typing import Any
from unittest.mock import MagicMock

from astrometricslib import AbstractCatalogAccess, Astrometrics, CatalogAccess, StellarObject, Target


class MockCatalogAccess(AbstractCatalogAccess):
    """A mock CatalogAccess for testing in-memory data flows."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.targets = [Target(id="M 31"), Target(id="Orion")]
        self.stellar_objects = [StellarObject(id="Star1"), StellarObject(id="Star2")]

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Return the in-memory targets or stellar objects.

        Returns
        -------
        Any
            The in-memory list for `target_catalog`/`stellar_catalog`, or
            `None` otherwise.
        """
        if dataset_type == "target_catalog":
            return self.targets
        elif dataset_type == "stellar_catalog":
            return self.stellar_objects
        return None

    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Store obj as the in-memory targets or stellar objects."""
        if dataset_type == "target_catalog":
            self.targets = obj
        elif dataset_type == "stellar_catalog":
            self.stellar_objects = obj

    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Return `False` always; this mock never reports existence.

        Returns
        -------
        bool
            Always `False`.
        """
        return False

    def get_local_path(self, dataset_type: str, selector: dict[str, Any]) -> str:
        """Return a fixed placeholder path for any dataset type.

        Returns
        -------
        str
            The fixed placeholder path `"/mock/path"`.
        """
        return "/mock/path"

    def list_star_summaries(self, target_id=None, limit=None) -> list:  # ruff: ignore[missing-type-function-argument]
        """Return no summaries; this mock keeps no indexed columns.

        Returns
        -------
        list
            Always empty.
        """
        return []

    def list_stars_in_region(self, ra_degrees, dec_degrees, radius_degrees) -> list:  # ruff: ignore[missing-type-function-argument]
        """Return no stars; this mock keeps no indexed columns.

        Returns
        -------
        list
            Always empty.
        """
        return []

    def list_position_only_stars(self, target_id=None) -> list:  # ruff: ignore[missing-type-function-argument]
        """Return no positions; this mock keeps no indexed columns.

        Returns
        -------
        list
            Always empty.
        """
        return []


def test_disk_butler_instantiation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies that CatalogAccess can be instantiated with default config."""
    catalog_access = CatalogAccess()
    assert catalog_access.config is not None


def test_mock_catalog_access_injection():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a mock CatalogAccess can be injected into the facade."""
    mock_catalog_access = MockCatalogAccess()
    astrometrics = Astrometrics(catalog_access=mock_catalog_access)

    # Verify hydration used the mock catalog_access
    targets = astrometrics.targets.list()
    assert len(targets) == 2
    assert targets[0].id == "M 31"
    assert targets[1].id == "Orion"

    # Verify saving routes back to mock catalog_access
    astrometrics.targets.save()
    assert len(mock_catalog_access.targets) == 2


def test_disk_butler_caches_stellar_catalog_reads(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify repeated stellar_catalog reads avoid redundant disk I/O."""
    mock_config = MagicMock()
    catalog_access = CatalogAccess(config=mock_config)

    mock_load = mocker.patch.object(
        catalog_access._generic,
        "get_all",
        return_value=[StellarObject(id="Star1")],
    )

    first = catalog_access.get("stellar_catalog", {})
    second = catalog_access.get("stellar_catalog", {})

    assert mock_load.call_count == 1
    assert first is second


def test_disk_butler_put_refreshes_stellar_catalog_cache(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify put() writes through to disk and refreshes the cache."""
    mock_config = MagicMock()
    catalog_access = CatalogAccess(config=mock_config)

    mock_load = mocker.patch.object(
        catalog_access._generic,
        "get_all",
        return_value=[StellarObject(id="Star1")],
    )
    mock_save = mocker.patch.object(catalog_access._generic, "put_all")

    updated = [StellarObject(id="Star2")]
    catalog_access.put(updated, "stellar_catalog", {})
    result = catalog_access.get("stellar_catalog", {})

    assert mock_save.call_count == 1
    assert result == updated
    assert mock_load.call_count == 0


def test_catalog_access_list_star_summaries_reads_the_stellar_catalog(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify list_star_summaries reaches the real catalog registration.

    Regression coverage for the target_id-indexed browsing path added
    alongside local_database's lightweight summary loader: confirms the
    DatasetSpec registered in this module actually has target_id
    available to filter on, and that it filters correctly.
    """
    from astrometricslib.utilities.config_loader import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})

    catalog_access = CatalogAccess(config=config)
    in_field = StellarObject(id="InField", name="InField")
    in_field.target_ids = ["M 13"]
    out_of_field = StellarObject(id="OutOfField", name="OutOfField")
    out_of_field.target_ids = ["M 81"]
    catalog_access.put([in_field, out_of_field], "stellar_catalog", {})

    summaries = catalog_access.list_star_summaries(target_id="M 13")

    assert [(star.id, star.name) for star in summaries] == [("InField", "InField")]


def test_catalog_access_list_position_only_stars_filters_by_prefix_and_target(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify only position-only stars of the asked-for target come back.

    Covers both filters against real SQL at once. The target narrowing
    runs as a substring LIKE for speed, so "M 1" would drag in a star
    that only belongs to "M 13" if the exact membership check after it
    were missing, and a star with a real catalog name must never be
    offered up for position matching however close it sits.
    """
    from astrometricslib.utilities.config_loader import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})

    catalog_access = CatalogAccess(config=config)

    wanted = StellarObject(id="FIELD_J1000-2600", name="FIELD_J1000-2600")
    wanted.right_ascension = 100.0
    wanted.declination = -26.0
    wanted.target_ids = ["M 1"]

    # Same target, but a named star -- not a candidate for position
    # matching, since it already has a catalog identity.
    named = StellarObject(id="HD 12345", name="HD 12345")
    named.right_ascension = 100.0
    named.declination = -26.0
    named.target_ids = ["M 1"]

    # Position-only, but "M 13" only looks like "M 1" to a LIKE.
    other_target = StellarObject(id="FIELD_J2000+3000", name="FIELD_J2000+3000")
    other_target.right_ascension = 200.0
    other_target.declination = 30.0
    other_target.target_ids = ["M 13"]

    catalog_access.put([wanted, named, other_target], "stellar_catalog", {})

    stars = catalog_access.list_position_only_stars(target_id="M 1")

    assert [star.id for star in stars] == ["FIELD_J1000-2600"]
    assert stars[0].right_ascension == 100.0  # ruff: ignore[float-equality-comparison]
    assert stars[0].target_ids == ["M 1"]


def test_disk_butler_stellar_catalog_has_a_target_id_index(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the real stellar_objects table gets its target_id index.

    A raw sqlite_master check rather than trusting the query results
    alone -- correct results don't prove the index exists, only
    that filtering is correct; a full scan would return the same rows.
    """
    import sqlite3

    from astrometricslib.utilities.config_loader import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})

    catalog_access = CatalogAccess(config=config)
    catalog_access.put([StellarObject(id="Polaris", name="Polaris")], "stellar_catalog", {})

    conn = sqlite3.connect(str(library_path / "astrometrics.db"))
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    conn.close()

    assert "idx_stellar_objects_target_id" in indexes


def _make_catalog_access_in(tmp_path) -> CatalogAccess:  # ruff: ignore[missing-type-function-argument]
    """Build a CatalogAccess that saves into an empty temporary library.

    Returns
    -------
    catalog_access : `CatalogAccess`
        Reads and writes a library inside `tmp_path`, never the real one.
    """
    from astrometricslib.utilities.config_loader import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return CatalogAccess(config=config)


def _make_star(star_id: str, ra: float, dec: float, magnitude=None, spectral_type="") -> StellarObject:  # ruff: ignore[missing-type-function-argument]
    """Build a star at a sky position for the region tests.

    Returns
    -------
    star : `StellarObject`
        A star with just the fields the region read uses.
    """
    star = StellarObject(id=star_id, name=star_id)
    star.right_ascension = ra
    star.declination = dec
    star.magnitude = magnitude
    star.spectral_type = spectral_type
    star.target_ids = ["NGC 7023"]
    return star


def _ids_inside_circle_by_the_slow_route(
    ra: float, dec: float, radius: float, stars: list[StellarObject]
) -> set[str]:
    """Pick the stars inside a circle the way the old sky map did.

    The old code loaded every star and asked astropy for each separation,
    so this is the answer the fast read has to agree with.

    Returns
    -------
    star_ids : `set` [`str`]
        The ids of the stars whose separation from the point is at most
        `radius` degrees.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    center = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    star_positions = SkyCoord(
        ra=[star.right_ascension for star in stars] * u.deg,
        dec=[star.declination for star in stars] * u.deg,
    )
    separations_degrees = center.separation(star_positions).deg
    return {
        star.id for star, separation in zip(stars, separations_degrees, strict=True) if separation <= radius
    }


def test_list_stars_in_region_matches_the_old_load_everything_answer(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the fast read returns the same stars astropy would pick.

    Circles are tried at the places a search box is most likely to go
    wrong: an ordinary spot, right on the 0/360 degree right ascension
    seam, and touching each pole.
    """
    import numpy as np

    random_generator = np.random.default_rng(seed=7)
    catalog_access = _make_catalog_access_in(tmp_path)
    stars = [
        _make_star(
            f"S{index}",
            float(random_generator.uniform(0.0, 360.0)),
            float(np.degrees(np.arcsin(random_generator.uniform(-1.0, 1.0)))),
        )
        for index in range(3000)
    ]
    catalog_access.put(stars, "stellar_catalog", {})

    circles = [
        (100.0, 20.0, 12.0),
        (2.0, 5.0, 15.0),  # spills over the seam at right ascension 0
        (358.0, -30.0, 20.0),  # spills over the seam from the other side
        (10.0, 88.0, 6.0),  # contains the north pole
        (200.0, -89.0, 4.0),  # contains the south pole
        (0.0, 0.0, 180.0),  # the whole sky
    ]
    for ra, dec, radius in circles:
        expected_ids = _ids_inside_circle_by_the_slow_route(ra, dec, radius, stars)

        found_ids = {star.id for star in catalog_access.list_stars_in_region(ra, dec, radius)}

        assert found_ids == expected_ids, (ra, dec, radius)


def test_list_stars_in_region_carries_magnitude_spectral_type_and_data_flags(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the summary has everything the sky map draws from a star."""
    catalog_access = _make_catalog_access_in(tmp_path)
    catalog_access.put(
        [_make_star("HD 1", 315.0, 68.0, magnitude=8.09, spectral_type="B3V")], "stellar_catalog", {}
    )

    (summary,) = catalog_access.list_stars_in_region(315.0, 68.0, 1.0)

    assert summary.id == "HD 1"
    assert summary.magnitude == 8.09  # ruff: ignore[float-equality-comparison]
    assert summary.spectral_type == "B3V"
    assert summary.target_ids == ["NGC 7023"]
    assert summary.has_photometry is False
    assert summary.has_spectra is False


def test_list_stars_in_region_leaves_out_stars_with_no_position(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a star at exactly (0, 0) is treated as having no position.

    The old sky map dropped these, so the fast read must too, even when
    the circle asked about happens to contain that point.
    """
    catalog_access = _make_catalog_access_in(tmp_path)
    no_position = StellarObject(id="NoPosition", name="NoPosition")
    no_position.right_ascension = 0.0
    no_position.declination = 0.0
    catalog_access.put([no_position, _make_star("Near", 1.0, 1.0)], "stellar_catalog", {})

    found_ids = [star.id for star in catalog_access.list_stars_in_region(0.0, 0.0, 5.0)]

    assert found_ids == ["Near"]


def test_region_read_is_answered_from_an_index_without_opening_the_stored_rows(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the region read's own query is answered from one index.

    Correct answers alone would not prove this, since a full scan gives
    the same rows, only far more slowly. The query text is the one the
    region read sends, so a column added to the read but not to the index
    makes this fail.
    """
    import sqlite3

    catalog_access = _make_catalog_access_in(tmp_path)
    catalog_access.put([_make_star("Polaris", 37.9, 89.3)], "stellar_catalog", {})
    catalog_access.list_stars_in_region(37.9, 89.3, 1.0)

    connection = sqlite3.connect(str(tmp_path / "library" / "astrometrics.db"))
    plan_rows = connection.execute(
        "EXPLAIN QUERY PLAN SELECT id, name, ra, dec, target_id, has_spectra, has_photometry, magnitude, "
        "spectral_type FROM stellar_objects WHERE dec BETWEEN 10 AND 20 AND ra BETWEEN 1 AND 2 "
        "AND magnitude BETWEEN -2 AND 12"
    ).fetchall()
    connection.close()

    plan_text = " ".join(str(row) for row in plan_rows)
    assert "COVERING INDEX" in plan_text
    assert "idx_stellar_objects_dec_ra_magnitude" in plan_text


def test_spectral_type_is_backfilled_for_stars_saved_before_the_column_existed(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an older database gets spectral types copied out of its JSON.

    The table is written by hand the way the app used to write it, with
    no spectral_type column, so the first read has to add and fill it.
    """
    import json
    import sqlite3

    catalog_access = _make_catalog_access_in(tmp_path)
    database_path = tmp_path / "library" / "astrometrics.db"
    connection = sqlite3.connect(str(database_path))
    connection.execute(
        "CREATE TABLE stellar_objects (id TEXT PRIMARY KEY, target_id TEXT, name TEXT, ra REAL, dec REAL, "
        "magnitude REAL, data_json TEXT, has_spectra INTEGER, has_photometry INTEGER)"
    )
    payload = json.dumps({"id": "HD 2", "spectralType": "K0III"})
    connection.execute(
        "INSERT INTO stellar_objects VALUES ('HD 2', 'NGC 7023', 'HD 2', 315.0, 68.0, 7.5, ?, 0, 0)",
        (payload,),
    )
    connection.commit()
    connection.close()

    (summary,) = catalog_access.list_stars_in_region(315.0, 68.0, 1.0)

    assert summary.spectral_type == "K0III"


def test_list_stars_in_region_can_keep_only_stars_in_a_magnitude_range(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify magnitude_range keeps stars inside it, ends included.

    A star with no saved magnitude is left out, since it has no value to
    compare with the range.
    """
    catalog_access = _make_catalog_access_in(tmp_path)
    catalog_access.put(
        [
            _make_star("TooBright", 10.0, 10.0, magnitude=-3.0),
            _make_star("LowEdge", 10.1, 10.0, magnitude=-2.0),
            _make_star("Middle", 10.2, 10.0, magnitude=8.0),
            _make_star("HighEdge", 10.3, 10.0, magnitude=12.0),
            _make_star("TooFaint", 10.4, 10.0, magnitude=12.5),
            _make_star("NoMagnitude", 10.5, 10.0, magnitude=None),
        ],
        "stellar_catalog",
        {},
    )

    kept = catalog_access.list_stars_in_region(10.0, 10.0, 5.0, magnitude_range=(-2.0, 12.0))
    everything = catalog_access.list_stars_in_region(10.0, 10.0, 5.0)

    assert sorted(star.id for star in kept) == ["HighEdge", "LowEdge", "Middle"]
    assert len(everything) == 6
