"""Purpose: Tests for the position-only stellar catalog duplicate cleanup.

Description: Verifies the pure clustering/merge logic
`reconcile_position_only_star_catalog` depends on, and exercises the full
find-clusters -> apply -> delete flow end to end against a throwaway
isolated catalog database -- never the real one.
"""

from astrometricslib.data_access.catalog_access import CatalogAccess
from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.scripts.reconcile_position_only_star_catalog import (
    _is_empty_value,
    _merge_duplicate_into_survivor,
    apply_clusters,
    cluster_position_only_rows,
    find_position_only_clusters,
)
from astrometricslib.utilities.config_loader import AppConfiguration


def _make_isolated_config(tmp_path) -> AppConfiguration:  # ruff: ignore[missing-type-function-argument]
    """Build an AppConfiguration pointed at a fresh, empty tmp_path library.

    Returns
    -------
    AppConfiguration
        A configuration pointed at a fresh, empty library under tmp_path.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    frames_path = library_path / "frames"
    frames_path.mkdir(parents=True)

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path), "frames_path": str(frames_path)}})
    return config


class _FakeAstrometrics:
    """Minimal stand-in exposing only what this script's functions read."""

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        self.config = config
        self.catalog_access = CatalogAccess(config)


def _row(id_: str, ra: float, dec: float, target_id: str = "M42") -> dict:
    return {"id": id_, "ra": ra, "dec": dec, "target_id": target_id}


def test_cluster_position_only_rows_groups_nearby_positions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify rows within the match radius cluster and distant ones don't."""
    close_pair = [
        _row("FIELD_J083344.3000-263740.0000", 128.834300, -26.627778),
        _row("FIELD_J083344.3050-263739.9980", 128.834305, -26.627770),
    ]
    distant = _row("FIELD_J083744.3000-263740.0000", 129.334300, -26.627778)

    clusters = cluster_position_only_rows([*close_pair, distant])

    sizes = sorted(len(cluster) for cluster in clusters)
    assert sizes == [1, 2]


def test_cluster_position_only_rows_chains_through_intermediate_points():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify clustering chains: A-B and B-C close, A-C not directly close.

    A chain of independent re-solves lands each new solve near the
    *previous* one, not necessarily near the very first -- three rows
    a few arcsec apart in sequence must still collapse to one cluster
    even though the first and last are further apart than the radius
    on their own.
    """
    # ~3 arcsec steps at this declination.
    step_deg = 3.0 / 3600.0 / 0.894  # roughly correct for cos(dec) at -26.6
    rows = [_row(f"FIELD_J{i}", 128.8343 + i * step_deg, -26.627778) for i in range(4)]

    clusters = cluster_position_only_rows(rows)

    assert len(clusters) == 1
    assert len(clusters[0]) == 4


def test_cluster_position_only_rows_handles_zero_and_one_row():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the trivial cases return without touching the scipy machinery."""
    assert cluster_position_only_rows([]) == []
    single = _row("FIELD_J1", 128.0, -26.0)
    assert cluster_position_only_rows([single]) == [[single]]


def test_is_empty_value_covers_every_shape_of_empty():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify None, blanks, empty lists, and empty light curves are empty."""
    assert _is_empty_value(None)
    assert _is_empty_value("")
    assert _is_empty_value("   ")
    assert _is_empty_value([])
    assert _is_empty_value({})
    assert _is_empty_value(LightCurve())
    assert not _is_empty_value("Vega")
    assert not _is_empty_value(0.0)
    assert not _is_empty_value([1])
    assert not _is_empty_value(LightCurve(fluxes=[1.0]))


def test_merge_duplicate_into_survivor_fills_gaps_without_overwriting():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a duplicate fills empty fields but never overwrites a set one."""
    survivor = StellarObject(id="FIELD_JA", name="FIELD_JA")
    survivor.magnitude = 12.5
    survivor.target_ids = ["M42"]

    duplicate = StellarObject(id="FIELD_JB", name="FIELD_JB")
    duplicate.magnitude = 99.9  # must NOT overwrite the survivor's own value
    duplicate.light_curve = LightCurve(fluxes=[1.0, 2.0, 3.0])
    duplicate.spectral_type = "A0V"
    duplicate.target_ids = ["M42", "M43"]

    _merge_duplicate_into_survivor(survivor, duplicate)

    assert survivor.magnitude == 12.5  # ruff: ignore[float-equality-comparison]
    assert survivor.light_curve.fluxes == [1.0, 2.0, 3.0]
    assert survivor.spectral_type == "A0V"
    assert survivor.target_ids == ["M42", "M43"]


def test_merge_duplicate_into_survivor_never_touches_identity_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify id/name/is_catalog_identified never copy from a duplicate."""
    survivor = StellarObject(id="FIELD_JA", name="FIELD_JA")
    duplicate = StellarObject(id="FIELD_JB", name="FIELD_JB")
    duplicate.is_catalog_identified = True

    _merge_duplicate_into_survivor(survivor, duplicate)

    assert survivor.id == "FIELD_JA"
    assert survivor.name == "FIELD_JA"
    assert survivor.is_catalog_identified is False


def test_find_and_apply_clusters_merges_and_deletes_against_a_real_catalog(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """End-to-end: two duplicate rows in a throwaway catalog collapse to one.

    Never touches the real library -- everything here runs against a
    fresh, isolated tmp_path database.
    """
    config = _make_isolated_config(tmp_path)
    astrometrics = _FakeAstrometrics(config)

    survivor = StellarObject(id="FIELD_J083344.3000-263740.0000", name="FIELD_J083344.3000-263740.0000")
    survivor.right_ascension = 128.834300
    survivor.declination = -26.627778
    survivor.magnitude = 15.2
    survivor.target_ids = ["M42"]

    duplicate = StellarObject(id="FIELD_J083344.3050-263739.9980", name="FIELD_J083344.3050-263739.9980")
    duplicate.right_ascension = 128.834305
    duplicate.declination = -26.627770
    duplicate.light_curve = LightCurve(fluxes=[10.0, 11.0])
    duplicate.target_ids = ["M42"]

    unrelated = StellarObject(id="FIELD_J090000.0000-260000.0000", name="FIELD_J090000.0000-260000.0000")
    unrelated.right_ascension = 135.0
    unrelated.declination = -26.0
    unrelated.target_ids = ["M42"]

    astrometrics.catalog_access.merge_and_record(
        "stellar_catalog", [survivor, duplicate, unrelated], lambda _existing, updated: updated
    )

    clusters_by_target = find_position_only_clusters(astrometrics)
    assert set(clusters_by_target) == {"M42"}
    clusters = clusters_by_target["M42"]
    assert len(clusters) == 1
    assert {row["id"] for row in clusters[0]} == {survivor.id, duplicate.id}

    rows_removed = apply_clusters(astrometrics, clusters_by_target)

    assert rows_removed == 1
    remaining = astrometrics.catalog_access.get_by_ids(
        "stellar_catalog", [survivor.id, duplicate.id, unrelated.id]
    )
    remaining_by_id = {obj.id: obj for obj in remaining}
    assert set(remaining_by_id) == {survivor.id, unrelated.id}
    # The duplicate's light curve was gap-filled onto the surviving row,
    # not lost when the duplicate's own row was deleted.
    assert remaining_by_id[survivor.id].light_curve.fluxes == [10.0, 11.0]
    assert remaining_by_id[survivor.id].magnitude == 15.2  # ruff: ignore[float-equality-comparison]


def test_find_position_only_clusters_respects_target_id_filter(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify --target scoping only surfaces one target's clusters."""
    config = _make_isolated_config(tmp_path)
    astrometrics = _FakeAstrometrics(config)

    for target_id in ("M42", "M43"):
        first = StellarObject(id=f"FIELD_J_{target_id}_A", name=f"FIELD_J_{target_id}_A")
        first.right_ascension = 100.0
        first.declination = 10.0
        first.target_ids = [target_id]
        second = StellarObject(id=f"FIELD_J_{target_id}_B", name=f"FIELD_J_{target_id}_B")
        second.right_ascension = 100.0001
        second.declination = 10.0001
        second.target_ids = [target_id]
        astrometrics.catalog_access.merge_and_record(
            "stellar_catalog", [first, second], lambda _existing, updated: updated
        )

    clusters_by_target = find_position_only_clusters(astrometrics, target_ids=["M42"])

    assert set(clusters_by_target) == {"M42"}
