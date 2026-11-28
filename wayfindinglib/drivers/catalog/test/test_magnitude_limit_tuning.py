"""Tests for the deep star catalog's magnitude-limit tuner.

The statistic and the star filter are checked with plain numbers, and the
tuner with a fake library, so no database is read.
"""

from types import SimpleNamespace

import pytest

from astrometricslib._internal.parameters import collect_tuners
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.drivers.catalog.deep_star_catalog_builder import DEFAULT_MAGNITUDE_LIMIT
from wayfindinglib.drivers.catalog.magnitude_limit_tuning import (
    MAGNITUDE_LIMIT_TUNER,
    gaia_magnitudes_of,
    magnitude_limit_from_magnitudes,
)


def _row(star_id: str, magnitude: float | None, real: bool = True) -> dict:
    """Build a star summary row.

    Parameters
    ----------
    star_id : `str`
        The star id.
    magnitude : `float` or `None`
        The catalog magnitude.
    real : `bool`, optional
        Whether it counts as a real catalog magnitude.

    Returns
    -------
    row : `dict`
        The row.
    """
    return {"id": star_id, "magnitude": magnitude, "hasCatalogMagnitude": real}


def _inputs(rows: list[dict] | Exception) -> TuningInputs:
    """Build tuning inputs with a fake library that answers a star query.

    Parameters
    ----------
    rows : `list` [`dict`] or `Exception`
        The summary rows to answer, or an error to raise.

    Returns
    -------
    inputs : `TuningInputs`
        Inputs with a ``"wayfinder"`` service.
    """

    def query(**_kwargs: object) -> list[SimpleNamespace]:
        """Answer like `StellarCatalog.query`, one star record per row.

        Returns
        -------
        stars : `list` [`types.SimpleNamespace`]
            Stand-ins with ``id``, ``magnitude`` and ``has_catalog_magnitude``.
        """
        if isinstance(rows, Exception):
            raise rows
        return [
            SimpleNamespace(
                id=row["id"],
                magnitude=row.get("magnitude"),
                has_catalog_magnitude=row.get("hasCatalogMagnitude"),
            )
            for row in rows
        ]

    wayfinder = SimpleNamespace(astrometrics=SimpleNamespace(stars=SimpleNamespace(query=query)))
    return TuningInputs(SimpleNamespace(camera_name="cam"), None, None, {"wayfinder": wayfinder})  # type: ignore[arg-type]


def test_limit_rounds_the_percentile_up_to_the_step() -> None:
    """The 95th percentile rounds up to the next half magnitude."""
    magnitudes = [10.0 + 0.01 * index for index in range(101)]  # 10.00 to 11.00
    assert magnitude_limit_from_magnitudes(magnitudes, 95.0, 0.5) == pytest.approx(11.0)
    assert magnitude_limit_from_magnitudes([16.0] * 10, 95.0, 0.5) == pytest.approx(16.0)
    assert magnitude_limit_from_magnitudes([16.01] * 10, 95.0, 0.5) == pytest.approx(16.5)


def test_gaia_magnitudes_keep_only_real_gaia_magnitudes() -> None:
    """Other bands, missing and unreal magnitudes are dropped."""
    rows = [
        _row("Gaia DR3 1", 15.0),
        _row("TYC 1-2-3", 9.0),
        _row("Gaia DR3 2", None),
        _row("Gaia DR3 3", -9.0, real=False),
        _row("Gaia DR3 4", float("nan")),
    ]
    assert gaia_magnitudes_of(rows) == [15.0]


def test_tuner_gives_the_limit_and_the_sample() -> None:
    """The tuner returns the rounded percentile and the star count."""
    rows = [_row(f"Gaia DR3 {i}", 12.0 + 5.0 * i / 299) for i in range(300)]
    result = MAGNITUDE_LIMIT_TUNER.tune(_inputs(rows))
    assert result.values[DEFAULT_MAGNITUDE_LIMIT.name] == pytest.approx(17.0)
    assert result.sample == 300


def test_tuner_skips_without_gaia_stars_or_without_the_library() -> None:
    """No usable stars, an unreadable library or a missing service skip."""
    with pytest.raises(TuningSkippedError):
        MAGNITUDE_LIMIT_TUNER.tune(_inputs([_row("TYC 1", 9.0)]))
    with pytest.raises(TuningSkippedError):
        MAGNITUDE_LIMIT_TUNER.tune(_inputs(OSError("locked")))
    with pytest.raises(TuningSkippedError):
        MAGNITUDE_LIMIT_TUNER.tune(TuningInputs(SimpleNamespace(), None, None, {}))  # type: ignore[arg-type]


def test_collect_tuners_finds_the_tuner_and_its_value_has_a_minimum_sample() -> None:
    """The tuner is found once, and the value declares a minimum sample."""
    found = {tuner.name: tuner for tuner in collect_tuners(("astrometricslib", "wayfindinglib"))}
    assert found[MAGNITUDE_LIMIT_TUNER.name] is MAGNITUDE_LIMIT_TUNER
    assert DEFAULT_MAGNITUDE_LIMIT.minimum_sample == 200
    assert not DEFAULT_MAGNITUDE_LIMIT.pending_conversion
