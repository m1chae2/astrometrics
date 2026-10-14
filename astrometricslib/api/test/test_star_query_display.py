"""Tests for the display features of `StellarCatalog.query`.

These cover what the app's star browser, class browser and image overlay
need from the library: single-frame detections hidden by default, the search
and photometry filters, the "most useful first" and "best spectrum match
first" orders, the per-target counts, and stars placed in pixels on a
target's image. Each test runs against a real `CatalogAccess` over an empty
temporary library.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.stellar_source import (
    PhotometryResult,
    SpectroscopyResult,
    StellarObject,
    has_catalog_magnitude,
)
from astrometricslib.models.target import Target, TargetStackingResult
from astrometricslib.pipelines.shared.star_catalog_queries import is_unresolved_detection

DETECTION_ID = "M13:2026-05-01:100:30:Star_7"


def _catalog(tmp_path: Path) -> StellarCatalog:
    """Build a star catalog over a fresh, empty library.

    Returns
    -------
    catalog : `StellarCatalog`
        A catalog whose storage is a real `CatalogAccess`.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return StellarCatalog(config, CatalogAccess(config))


def _spectrum(rms: float | None = None) -> SpectroscopyResult:
    """Build a minimal measured spectrum.

    Returns
    -------
    spectrum : `SpectroscopyResult`
        One wavelength, with the given match difference.
    """
    return SpectroscopyResult(
        wavelengths_angstrom=[5000.0], intensities=[1.0], self_determined_spectral_type_rms=rms
    )


def _light_curve() -> PhotometryResult:
    """Build a minimal light curve.

    Returns
    -------
    light_curve : `PhotometryResult`
        One measured point.
    """
    return PhotometryResult(timestamps=["2026-05-01T03:00:00Z"], magnitudes=[11.2])


@pytest.fixture
def catalog(tmp_path: Path) -> StellarCatalog:
    """Make a catalog with a mix of stars in target M 13.

    Returns
    -------
    catalog : `StellarCatalog`
        Holds a single-frame detection, a position-only star, stars with and
        without spectra or photometry, and stars with real, zero and
        instrumental magnitudes.
    """
    catalog = _catalog(tmp_path)
    stars = [
        StellarObject(id="HD 1", name="Alpha", target_ids=["M 13"], ra=250.40, dec=36.46, magnitude=9.0),
        StellarObject(id="HD 2", name="Beta", target_ids=["M 13"], ra=250.41, dec=36.47, magnitude=6.0),
        StellarObject(id="HD 3", name="Gamma", target_ids=["M 13"], ra=250.42, dec=36.45, magnitude=0.0),
        StellarObject(id="FIELD_J1", name="", target_ids=["M 13"], ra=250.43, dec=36.44, magnitude=-12.5),
        StellarObject(id=DETECTION_ID, name="", target_ids=["M 13"], ra=250.44, dec=36.43, magnitude=8.0),
        StellarObject(id="HD 9", name="Far", target_ids=["NGC 1"], ra=10.0, dec=5.0, magnitude=5.0),
    ]
    stars[0].spectroscopy = _spectrum(0.04)
    stars[0].spectral_type = "G2V"
    stars[1].photometry = _light_curve()
    stars[1].spectral_type = "G8III"
    stars[2].spectroscopy = _spectrum(0.02)
    stars[2].spectral_type = "G5"
    stars[4].spectral_type = "G0"
    stars[5].spectral_type = "K1"
    catalog.catalog_access.put(stars, "stellar_catalog", {})
    return catalog


def test_single_frame_detections_are_recognized_by_id() -> None:
    """Only ids ending in ':Star_<n>' are single-frame detections."""
    assert is_unresolved_detection(DETECTION_ID)
    assert not is_unresolved_detection("Star_12")
    assert not is_unresolved_detection("HD 1234")


def test_single_frame_detections_are_hidden_unless_asked_for(catalog: StellarCatalog) -> None:
    """The default hides detections; include_unresolved brings them back."""
    hidden = catalog.query(target_id="M 13", detail="ids", limit=None).ids
    shown = catalog.query(target_id="M 13", detail="ids", include_unresolved=True, limit=None).ids

    assert DETECTION_ID not in hidden
    assert DETECTION_ID in shown


def test_class_counts_skip_single_frame_detections(catalog: StellarCatalog) -> None:
    """The class browser's counts leave detections out by default."""
    counts = {row["spectralClass"]: row["count"] for row in catalog.query(detail="class_counts").classes}
    with_detections = catalog.query(detail="class_counts", include_unresolved=True).classes

    assert counts == {"G": 3, "K": 1}
    assert {row["spectralClass"]: row["count"] for row in with_detections}["G"] == 4


def test_target_counts_report_stars_and_data_per_target(catalog: StellarCatalog) -> None:
    """Each target gets its star count and spectra and photometry marks."""
    counts = catalog.query(detail="target_counts").target_counts

    assert counts["M 13"].star_count == 4
    assert counts["M 13"].has_spectra and counts["M 13"].has_photometry
    assert counts["NGC 1"].model_dump(by_alias=True) == {
        "starCount": 1,
        "hasSpectra": False,
        "hasPhotometry": False,
    }


def test_search_and_photometry_filters(catalog: StellarCatalog) -> None:
    """Search matches id or name text; has_photometry keeps light curves."""
    assert catalog.query(search="gam", detail="ids").ids == ["HD 3"]
    assert catalog.query(search="hd ", detail="ids", limit=None).ids == ["HD 1", "HD 2", "HD 3", "HD 9"]
    assert catalog.query(has_photometry=True, detail="ids").ids == ["HD 2"]


def test_summary_rows_say_whether_the_magnitude_is_a_catalog_one(catalog: StellarCatalog) -> None:
    """Zero and instrumental magnitudes are not catalog magnitudes."""
    rows = {row["id"]: row for row in catalog.query(target_id="M 13", limit=None).stars}

    assert rows["HD 2"]["hasCatalogMagnitude"] is True
    assert rows["HD 3"]["hasCatalogMagnitude"] is False
    assert rows["FIELD_J1"]["hasCatalogMagnitude"] is False


def test_catalog_magnitude_rule() -> None:
    """The rule rejects missing, zero, instrumental and non-number values."""
    assert has_catalog_magnitude(6.5)
    assert has_catalog_magnitude(-1.4)
    for value in (None, "", 0, 0.0, -12.0, float("nan"), True):
        assert not has_catalog_magnitude(value)
    assert StellarObject(id="x", magnitude=7.0).model_dump(by_alias=True)["hasCatalogMagnitude"] is True


def test_useful_order_puts_spectra_names_photometry_then_brightness_first(catalog: StellarCatalog) -> None:
    """Spectra first, then named stars, then photometry, then brightest."""
    ids = catalog.query(target_id="M 13", detail="ids", order="useful", limit=None).ids

    assert ids == ["HD 1", "HD 3", "HD 2", "FIELD_J1"]


def test_match_order_ranks_by_spectrum_match_and_reports_it(catalog: StellarCatalog) -> None:
    """The best-matched star comes first and rows carry the difference."""
    rows = catalog.query(spectral_class="G", order="match", limit=None).stars

    assert [row["id"] for row in rows] == ["HD 3", "HD 1", "HD 2"]
    assert [row["selfDeterminedSpectralTypeRms"] for row in rows] == [0.02, 0.04, None]


def test_arguments_a_detail_does_not_use_are_refused(catalog: StellarCatalog) -> None:
    """Whole-library and overlay details refuse filters and selectors."""
    with pytest.raises(InvalidArgumentError, match="does not use: search"):
        catalog.query(detail="class_counts", search="HD")
    with pytest.raises(InvalidArgumentError, match="does not use: include_unresolved"):
        catalog.query(detail="stats", include_unresolved=True)
    with pytest.raises(InvalidArgumentError, match="needs target_id"):
        catalog.query(detail="overlay")
    with pytest.raises(InvalidArgumentError, match="does not use: has_spectra"):
        catalog.query(target_id="M 13", detail="overlay", has_spectra=True)
    with pytest.raises(InvalidArgumentError, match="order"):
        catalog.query(order="brightest")


def _write_reference_image(path: Path) -> None:
    """Write a 200 by 100 pixel FITS image whose WCS centers on M 13's stars.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the image.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [250.42, 36.45]
    wcs.wcs.crpix = [100.0, 50.0]
    wcs.wcs.cdelt = [-0.00005, 0.00005]
    header = wcs.to_header()
    fits.PrimaryHDU(data=np.zeros((100, 200), dtype=np.float32), header=header).writeto(path)


def test_overlay_places_stars_through_the_image_wcs(catalog: StellarCatalog, tmp_path: Path) -> None:
    """Stars inside the image get pixel positions, most useful first."""
    image = tmp_path / "m13_stack.fits"
    _write_reference_image(image)
    target = Target(id="M 13", stacking=TargetStackingResult(stacked_image=str(image)))
    catalog.catalog_access.put([target], "target_catalog", {})

    overlay = catalog.query(target_id="M_13", detail="overlay", limit=10).overlay

    assert [star.id for star in overlay] == ["HD 3"]
    placed = overlay[0]
    assert placed.x == pytest.approx(99.0, abs=0.2)
    assert placed.y == pytest.approx(49.0, abs=0.2)
    assert placed.model_dump(by_alias=True)["referenceWidth"] == 200
    assert placed.reference_height == 100


def test_overlay_uses_saved_centers_when_the_image_has_no_wcs(tmp_path: Path) -> None:
    """Without a WCS, stars use the pixel center saved at detection."""
    catalog = _catalog(tmp_path)
    centered = StellarObject(
        id="HD 5", name="Delta", target_ids=["M 13"], magnitude=7.0, is_catalog_identified=True, radius_px=3.5
    )
    centered.star_data = {"xcentroid": 12.25, "ycentroid": 40.0}
    catalog.catalog_access.put(
        [centered, StellarObject(id="HD 6", name="Unplaced", target_ids=["M 13"], magnitude=6.0)],
        "stellar_catalog",
        {},
    )

    overlay = catalog.query(target_id="M 13", detail="overlay").overlay

    assert [(star.id, star.x, star.y, star.radius_px) for star in overlay] == [("HD 5", 12.2, 40.0, 3.5)]
    assert overlay[0].is_catalog_identified


def test_catalog_magnitude_filter_in_a_region(catalog: StellarCatalog) -> None:
    """A region search can keep only stars with a real catalog magnitude."""
    ids = catalog.query(
        ra_deg=250.42, dec_deg=36.45, radius_deg=0.5, has_catalog_magnitude=True, detail="ids", limit=None
    ).ids
    others = catalog.query(
        ra_deg=250.42, dec_deg=36.45, radius_deg=0.5, has_catalog_magnitude=False, detail="ids", limit=None
    ).ids

    assert ids == ["HD 1", "HD 2"]
    assert others == ["FIELD_J1", "HD 3"]


def test_overlay_never_labels_clusters_or_field_detections(tmp_path: Path) -> None:
    """Cluster entries, single-frame detections and Star_ ids get no label."""
    catalog = _catalog(tmp_path)
    centers = {"xcentroid": 50.0, "ycentroid": 60.0}
    catalog.catalog_access.put(
        [
            StellarObject(
                id="M_13_Cluster", target_ids=["M 13"], stellar_spectral_type="Cluster", star_data=centers
            ),
            StellarObject(id="M 13:2026-01-14:0:0:Star_42", target_ids=["M 13"], star_data=centers),
            StellarObject(id="Star_99", target_ids=["M 13"], star_data=centers),
            StellarObject(id="FIELD_J249.7726+35.4880", target_ids=["M 13"], star_data=centers),
        ],
        "stellar_catalog",
        {},
    )

    overlay = catalog.query(target_id="M 13", detail="overlay").overlay

    assert [star.id for star in overlay] == ["FIELD_J249.7726+35.4880"]


def test_overlay_puts_catalog_identified_stars_first_and_respects_the_limit(tmp_path: Path) -> None:
    """A catalog-identified star outranks brighter-numbered field stars."""
    catalog = _catalog(tmp_path)
    field_stars = [
        StellarObject(
            id=f"FIELD_J{n}",
            target_ids=["M 13"],
            magnitude=10.0 + n,
            star_data={"xcentroid": n, "ycentroid": n},
        )
        for n in range(10)
    ]
    identified = StellarObject(
        id="TYC 1234",
        name="HD 149757",
        target_ids=["M 13"],
        stellar_spectral_type="O9.5V",
        is_catalog_identified=True,
        magnitude=2.56,
        star_data={"xcentroid": 800.0, "ycentroid": 600.0},
    )
    catalog.catalog_access.put([*field_stars, identified], "stellar_catalog", {})

    overlay = catalog.query(target_id="M 13", detail="overlay", limit=3).overlay

    assert [star.id for star in overlay] == ["TYC 1234", "FIELD_J0", "FIELD_J1"]
    assert overlay[0].spectral_type == "O9.5V"
    assert overlay[0].model_dump(by_alias=True)["isCatalogIdentified"] is True
