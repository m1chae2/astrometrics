"""Tests that the noise model, alias peaks and amplitude are stored right.

The measurements must say what they rest on: an assumed gain or an unknown
site height makes a value ``assumed`` with the reason, a missing input makes
a ``not_measured`` row with its reason, and the airmass is never a default.
The configuration reads (site height, aperture) and the target star's choice
are tested with small stand-ins, and the whole step is run through the
registry's checks.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.models.stellar_source import PeriodogramResult, PhotometryResult, StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry.metrics import (
    NOISE_MODEL_MAGNITUDES,
    NOISE_MODEL_PARTS,
    PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG,
    PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG,
    PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG,
    PHOTOMETRY_TARGET_PHASE_COVERAGE_FRACTION,
    WINDOW_ALIAS_RANKS,
    model_noise_metric_name,
    window_alias_frequency_metric_name,
    window_alias_power_metric_name,
)
from astrometricslib.pipelines.photometry.post_processing.record_noise_budget import (
    ELEVATION_UNKNOWN_REASON,
    GAIN_ASSUMED_REASON,
    aperture_diameter_mm,
    dominant_session,
    noise_budget_inputs,
    noise_budget_measurements,
    session_zero_point,
    site_elevation_m,
)
from astrometricslib.pipelines.photometry.post_processing.record_period_sensitivity import (
    frame_times_days,
    window_alias_measurements,
)
from astrometricslib.pipelines.photometry.post_processing.record_target_amplitude import (
    TargetAmplitudeInputs,
    target_amplitude_inputs,
    target_amplitude_measurements,
)
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import CatalogBand
from astrometricslib.pipelines.photometry.processing.noise_budget import NoiseBudgetInputs, SessionNoiseInputs
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    VERDICT_DETECTED,
    VERDICT_POSSIBLE,
)
from astrometricslib.pipelines.photometry.processing.target_amplitude import FoldedAmplitude
from astrometricslib.pipelines.photometry.test.conformance_fixture import (
    _fixture_session_zero_points,
    photometry_fixture_frame_times_days,
    photometry_fixture_noise_inputs,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.record_measurements import check_measurement_against_registry

CONTEXT = PipelineRunContext(
    "M 99", "photometry", "1.3.0", "job-7", "job-7", datetime(2026, 10, 23, tzinfo=UTC)
)


def one(measurements: list[QualityMeasurement], metric: str) -> QualityMeasurement:
    """Pick the one measurement of a metric.

    Parameters
    ----------
    measurements : `list` [`QualityMeasurement`]
        The measurements.
    metric : `str`
        The metric name.

    Returns
    -------
    measurement : `QualityMeasurement`
        The only row of that metric.
    """
    found = [measurement for measurement in measurements if measurement.metric == metric]
    assert len(found) == 1, metric
    return found[0]


def with_session(inputs: NoiseBudgetInputs, **changes: object) -> NoiseBudgetInputs:
    """Copy noise inputs with some session fields replaced.

    Returns
    -------
    inputs : `NoiseBudgetInputs`
        The same inputs with the session changed.
    """
    session = SessionNoiseInputs(**{**inputs.session.__dict__, **changes})
    return NoiseBudgetInputs(**{**inputs.__dict__, "session": session})


# ---- the noise model -----------------------------------------------------


def test_a_full_set_of_inputs_gives_every_noise_row_measured() -> None:
    """With every input known, no noise row is assumed."""
    rows = noise_budget_measurements(photometry_fixture_noise_inputs(), CONTEXT)
    assert len(rows) == len(NOISE_MODEL_MAGNITUDES) * len(NOISE_MODEL_PARTS) + 1
    assert all(row.status is MeasurementStatus.MEASURED for row in rows)
    assert all(row.value is not None and row.value > 0 for row in rows)


def test_photon_noise_falls_and_sky_noise_rises_toward_faint_stars() -> None:
    """Photon noise in magnitudes rises toward faint stars; sky noise too."""
    rows = noise_budget_measurements(photometry_fixture_noise_inputs(), CONTEXT)
    photon = [
        one(rows, model_noise_metric_name("photon", magnitude)).value for magnitude in NOISE_MODEL_MAGNITUDES
    ]
    sky = [one(rows, model_noise_metric_name("sky", magnitude)).value for magnitude in NOISE_MODEL_MAGNITUDES]
    assert photon == sorted(photon)
    assert sky == sorted(sky)
    assert sky[-1] / sky[0] == pytest.approx(
        10.0 ** (0.4 * (NOISE_MODEL_MAGNITUDES[-1] - NOISE_MODEL_MAGNITUDES[0]))
    )


def test_scintillation_provenance_names_the_header_source_without_the_airmass_value() -> None:
    """The provenance names the header source and omits the airmass value."""
    inputs = photometry_fixture_noise_inputs()
    rows = noise_budget_measurements(inputs, CONTEXT)
    reason = one(rows, PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG).reason
    assert "airmass from the median of" in reason
    assert "frame headers" in reason
    assert f"{inputs.airmass:.2f}" not in reason


def test_an_unknown_site_height_makes_scintillation_assumed() -> None:
    """A site height of 0 means unknown, so sea level is assumed."""
    inputs = NoiseBudgetInputs(**{**photometry_fixture_noise_inputs().__dict__, "site_elevation_m": None})
    rows = noise_budget_measurements(inputs, CONTEXT)
    scintillation = one(rows, PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG)
    assert scintillation.status is MeasurementStatus.ASSUMED
    assert ELEVATION_UNKNOWN_REASON in scintillation.reason
    known = one(
        noise_budget_measurements(photometry_fixture_noise_inputs(), CONTEXT),
        PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG,
    )
    assert scintillation.value > known.value
    photon = one(rows, model_noise_metric_name("photon", 12))
    assert photon.status is MeasurementStatus.MEASURED


def test_an_assumed_gain_marks_every_electron_based_row_assumed() -> None:
    """A guessed gain makes every electron-based row assumed."""
    rows = noise_budget_measurements(
        with_session(photometry_fixture_noise_inputs(), gain_is_assumed=True), CONTEXT
    )
    for part in NOISE_MODEL_PARTS:
        row = one(rows, model_noise_metric_name(part, 12))
        assert row.status is MeasurementStatus.ASSUMED
        assert GAIN_ASSUMED_REASON in row.reason
    assert one(rows, PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG).status is MeasurementStatus.MEASURED


def test_an_assumed_read_noise_leaves_sky_and_read_not_measured() -> None:
    """Without the read noise, sky and read are out; photon noise stays."""
    rows = noise_budget_measurements(
        with_session(photometry_fixture_noise_inputs(), read_noise_is_assumed=True, read_noise_e=0.0), CONTEXT
    )
    for part in ("sky", "read"):
        row = one(rows, model_noise_metric_name(part, 12))
        assert row.status is MeasurementStatus.NOT_MEASURED
        assert "read noise" in row.reason
    assert one(rows, model_noise_metric_name("photon", 12)).value is not None


def test_no_header_airmass_leaves_scintillation_not_measured_with_the_reason() -> None:
    """With no header airmass, scintillation is not guessed from 1.0."""
    inputs = NoiseBudgetInputs(**{
        **photometry_fixture_noise_inputs().__dict__,
        "airmass": None,
        "airmass_frame_count": 0,
        "airmass_reason": "no frame header of the run gave an airmass",
    })
    rows = noise_budget_measurements(inputs, CONTEXT)
    scintillation = one(rows, PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG)
    assert scintillation.value is None
    assert "airmass" in scintillation.reason
    assert one(rows, model_noise_metric_name("photon", 10)).value is not None


def test_no_zero_point_leaves_the_electron_terms_out_but_not_scintillation() -> None:
    """Scintillation needs no flux, so it survives a missing zero point."""
    inputs = NoiseBudgetInputs(**{
        **photometry_fixture_noise_inputs().__dict__,
        "zero_point_mag": None,
        "zero_point_reason": "no stars",
    })
    rows = noise_budget_measurements(inputs, CONTEXT)
    assert one(rows, model_noise_metric_name("photon", 12)).value is None
    assert "no stars" in one(rows, model_noise_metric_name("photon", 12)).reason
    assert one(rows, PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG).value is not None


def test_a_run_with_no_session_stores_every_row_as_not_measured() -> None:
    """With nothing gathered, all rows are present and none has a value."""
    rows = noise_budget_measurements(None, CONTEXT)
    assert len(rows) == len(NOISE_MODEL_MAGNITUDES) * len(NOISE_MODEL_PARTS) + 1
    assert all(row.value is None and row.reason for row in rows)


def test_every_noise_row_passes_the_registry_check() -> None:
    """Names, units, versions and levels agree with the declarations."""
    for row in noise_budget_measurements(photometry_fixture_noise_inputs(), CONTEXT):
        check_measurement_against_registry(row)


class _Config:
    """A stand-in configuration with a site height and optics."""

    def __init__(self, elevation: object = "0", optics: tuple[OpticConfig, ...] = ()) -> None:
        self._elevation = elevation
        self._optics = optics

    def get_value(self, section: str, key: str, fallback: object = None) -> object:
        """Give the site height.

        Returns
        -------
        value : `object`
            The height for the site's ``elevation`` key, else the fallback.
        """
        return self._elevation if (section, key) == ("Observatory.Location", "elevation") else fallback

    def get_observatory_setups(self) -> ObservatorySetups:
        """Give the optics.

        Returns
        -------
        setups : `ObservatorySetups`
            The optics given at construction.
        """
        return ObservatorySetups(optics=self._optics)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", None), ("1400", 1400.0), ("-5", None), ("unknown", None), (None, None), ("nan", None)],
)
def test_the_site_height_zero_means_unknown(raw: object, expected: float | None) -> None:
    """The configuration holds 0 for unknown; 0 is never sea level."""
    assert site_elevation_m(_Config(raw)) == expected


def test_the_aperture_is_the_focal_length_over_the_ratio_of_the_frames_optic() -> None:
    """405 mm at f/5.4 is 75 mm; other optics give a reason."""
    optics = (
        OpticConfig(name="Apertura 75Q", focal_length_mm=405.0, focal_ratio=5.4),
        OpticConfig(name="Lens", focal_length_mm=300.0),
    )
    frames = [SimpleNamespace(telescope="Apertura 75Q")] * 3 + [SimpleNamespace(telescope="Unknown")]
    diameter, reason = aperture_diameter_mm(frames, _Config(optics=optics))
    assert diameter == pytest.approx(75.0)
    assert reason == ""
    assert aperture_diameter_mm([SimpleNamespace(telescope="Lens")], _Config(optics=optics))[0] is None
    assert (
        "focal ratio" in aperture_diameter_mm([SimpleNamespace(telescope="Lens")], _Config(optics=optics))[1]
    )
    assert (
        "not listed" in aperture_diameter_mm([SimpleNamespace(telescope="Other")], _Config(optics=optics))[1]
    )
    assert (
        "no telescope"
        in aperture_diameter_mm([SimpleNamespace(telescope="Unknown")], _Config(optics=optics))[1]
    )


def test_the_dominant_session_is_the_one_with_most_points_and_has_its_own_zero_point() -> None:
    """The session with most points is used, with its own zero point."""
    small = SessionNoiseInputs("small", 10.0, 0.783, False, 3.8, False, 1.0e5, 10)
    big = SessionNoiseInputs("night-1", 10.0, 0.783, False, 3.8, False, 1.0e5, 900)
    empty = SessionNoiseInputs("empty", 10.0, 0.783, False, 3.8, False, None, 0)
    assert dominant_session([small, big, empty]) is big
    assert dominant_session([empty]) is None
    zero_points = [_fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")]
    value, count, reason, band = session_zero_point("night-1", zero_points)
    assert value == pytest.approx(21.5, abs=0.02)
    assert count == 12
    assert reason == ""
    assert band == "Gaia G"
    assert session_zero_point("other", zero_points)[0] is None


def test_the_inputs_of_a_run_are_gathered_from_sessions_frames_and_configuration() -> None:
    """Airmass comes from the frame records, never from a default."""
    optics = (OpticConfig(name="Apertura 75Q", focal_length_mm=405.0, focal_ratio=5.4),)
    frames = [SimpleNamespace(telescope="Apertura 75Q", airmass=value) for value in (1.2, None, 1.4)]
    session = SessionNoiseInputs("night-1", 10.0, 0.783, False, 3.8, False, 4.0e5, 500)
    zero_points = [_fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")]
    inputs = noise_budget_inputs([session], zero_points, frames, _Config("0", optics))
    assert inputs.session is session
    assert inputs.airmass == pytest.approx(1.3)
    assert inputs.airmass_frame_count == 2
    assert inputs.aperture_diameter_mm == pytest.approx(75.0)
    assert inputs.site_elevation_m is None
    assert inputs.band_name == "Gaia G"
    none_inputs = noise_budget_inputs([], [], [], _Config())
    assert none_inputs.session is None
    assert "no session" in none_inputs.zero_point_reason
    assert none_inputs.airmass is None


# ---- the alias peaks -----------------------------------------------------


def test_the_alias_rows_hold_a_frequency_and_a_power_for_each_rank() -> None:
    """Three nights give six rows, the strongest peak first."""
    rows = window_alias_measurements(photometry_fixture_frame_times_days(), CONTEXT)
    assert len(rows) == 2 * len(WINDOW_ALIAS_RANKS)
    first_power = one(rows, window_alias_power_metric_name(1)).value
    assert 0.0 < first_power <= 1.0
    assert one(rows, window_alias_frequency_metric_name(1)).value > 0
    powers = [one(rows, window_alias_power_metric_name(rank)).value for rank in WINDOW_ALIAS_RANKS]
    assert powers == sorted(powers, reverse=True)
    for row in rows:
        check_measurement_against_registry(row)


def test_a_single_night_stores_every_alias_row_as_not_measured() -> None:
    """One night has no daily echo, and each row says so."""
    rows = window_alias_measurements(np.arange(100) / 1440.0, CONTEXT)
    assert len(rows) == 6
    assert all(row.value is None and "single night" in row.reason for row in rows)


def test_frame_times_are_the_distinct_exposure_starts_of_all_the_stars() -> None:
    """Two stars measured in the same frames give each frame time once."""
    start = datetime(2026, 10, 3, 4, 0)
    stamps = [datetime(2026, 10, 3, 4, minute) for minute in range(3)]
    stars = [SimpleNamespace(photometry=PhotometryResult(timestamps=stamps)) for _ in range(2)]
    stars.append(SimpleNamespace(photometry=None))
    times = frame_times_days(stars)
    assert times.size == 3
    assert np.allclose(np.diff(times), 1.0 / 1440.0)
    assert times[0] == pytest.approx((start - datetime(1970, 1, 1)).total_seconds() / 86400.0)
    assert frame_times_days([]).size == 0


# ---- the target amplitude ------------------------------------------------

_CENTER_RA = 47.0422
_CENTER_DEC = 40.9556
_TARGET = Target(id="Algol")


def _light_curve_star(
    star_id: str = "Algol", offset_arcsec: float = 2.0, period: float | None = 2.87
) -> StellarObject:
    """Build a catalog-identified star near the target with a sine light curve.

    Returns
    -------
    star : `StellarObject`
        A star with a 0.4 mag sine of period 2.87 d over 12 cycles and, when
        `period` is given, a detected periodogram result.
    """
    rng = np.random.default_rng(8)
    days = np.sort(rng.uniform(0.0, 12.0 * 2.87, 600))
    epoch = datetime(2026, 9, 1)
    magnitude = 0.2 * np.sin(2 * np.pi * days / 2.87) + rng.normal(0.0, 0.002, days.size)
    flux = list(10.0 ** (-0.4 * magnitude))
    periodogram = (
        PeriodogramResult(best_period_days=period, verdict=VERDICT_DETECTED) if period is not None else None
    )
    return StellarObject(
        id=star_id,
        name=star_id,
        right_ascension=_CENTER_RA,
        declination=_CENTER_DEC + offset_arcsec / 3600.0,
        magnitude=2.1,
        is_catalog_identified=True,
        target_ids=["Algol"],
        photometry=PhotometryResult(
            timestamps=[epoch + timedelta(days=float(day)) for day in days],
            fluxes_normalized=flux,
            fluxes_detrended=flux,
            time_bjd_tdb=list(2461000.0 + days),
            periodogram=periodogram,
        ),
    )


class _Catalog:
    """A catalog stand-in that returns saved rows by id."""

    def __init__(self, rows: list[StellarObject]) -> None:
        self.rows = rows

    def get_by_ids(self, table: str, identifiers: list[str]) -> list[StellarObject]:
        """Give the saved rows for the ids.

        Returns
        -------
        rows : `list` [`StellarObject`]
            The rows whose ids were asked for.
        """
        assert table == "stellar_catalog"
        return [row for row in self.rows if row.id in identifiers]


def test_the_target_amplitude_comes_from_the_light_curve_and_the_catalog_separately() -> None:
    """The folded amplitude and the VSX range are two rows by source."""
    star = _light_curve_star()
    saved = StellarObject(
        id=star.id,
        name=star.name,
        vsxVariabilityType="EA/SD",
        vsxAmplitudeMag=1.21,
        vsxPeriodDays=2.867,
        vsxMagnitudeBand="V",
    )
    inputs = target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([saved]))
    assert inputs.period_source.startswith("the target star's own periodogram")
    rows = target_amplitude_measurements(inputs, CONTEXT)
    light_curve = one(rows, PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG)
    assert light_curve.value == pytest.approx(0.40, rel=0.1)
    assert light_curve.uncertainty > 0
    assert "periodogram" in light_curve.reason
    assert one(rows, PHOTOMETRY_TARGET_PHASE_COVERAGE_FRACTION).value == pytest.approx(1.0)
    catalog = one(rows, PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG)
    assert catalog.value == pytest.approx(1.21)
    assert "VSX" in catalog.reason
    for row in rows:
        check_measurement_against_registry(row)


def test_without_a_periodogram_the_vsx_period_is_used_and_named() -> None:
    """With no periodogram result, the fold uses the VSX period."""
    star = _light_curve_star(period=None)
    saved = StellarObject(
        id=star.id, name=star.name, vsxVariabilityType="EA/SD", vsxAmplitudeMag=1.21, vsxPeriodDays=2.87
    )
    inputs = target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([saved]))
    assert inputs.period_source == "the VSX period"
    assert inputs.period_days == pytest.approx(2.87)


def test_no_period_means_no_light_curve_amplitude_but_the_catalog_one_stays() -> None:
    """With neither periodogram nor VSX period there is nothing to fold on."""
    star = _light_curve_star(period=None)
    saved = StellarObject(id=star.id, name=star.name, vsxVariabilityType="EA/SD", vsxAmplitudeMag=1.21)
    rows = target_amplitude_measurements(
        target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([saved])), CONTEXT
    )
    light_curve = one(rows, PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG)
    assert light_curve.value is None
    assert "period" in light_curve.reason
    assert one(rows, PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG).value == pytest.approx(1.21)


def test_a_named_star_far_from_the_target_position_fails_the_cross_check() -> None:
    """The star that carries the name must also lie within 30 arcseconds."""
    star = _light_curve_star(offset_arcsec=120.0)
    inputs = target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([]))
    assert inputs.star_id == ""
    assert "farther than 30" in inputs.light_curve_reason
    assert "farther than 30" in inputs.catalog_reason


def test_a_neighbor_nearer_than_the_named_star_is_not_taken_for_the_target() -> None:
    """The match is by catalog name, so a nearer neighbor never stands in."""
    named = _light_curve_star(offset_arcsec=5.0)
    neighbor = _light_curve_star(star_id="HD 19356B", offset_arcsec=1.0)
    inputs = target_amplitude_inputs([neighbor, named], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([]))
    assert inputs.star_id == "Algol"
    assert inputs.separation_arcsec == pytest.approx(5.0, abs=0.1)


@pytest.mark.parametrize("name", ["M 13", "NGC 7000", "IC 434", "Moon"])
def test_a_target_that_is_not_a_star_gets_no_amplitude_rows(name: str) -> None:
    """A cluster, galaxy, nebula or planet has no single amplitude."""
    inputs = target_amplitude_inputs(
        [_light_curve_star(star_id=name)], Target(id=name), _CENTER_RA, _CENTER_DEC, _Catalog([])
    )
    assert inputs.star_id == ""
    rows = target_amplitude_measurements(inputs, CONTEXT)
    assert all(row.value is None and "not a single star" in row.reason for row in rows)


def test_a_possible_period_is_labeled_possible_in_the_amplitude_row() -> None:
    """A fold on a possible period carries that label with the amplitude."""
    star = _light_curve_star()
    star.photometry.periodogram = PeriodogramResult(best_period_days=2.87, verdict=VERDICT_POSSIBLE)
    inputs = target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([]))
    assert "possible, not confirmed" in inputs.period_source
    light_curve = one(
        target_amplitude_measurements(inputs, CONTEXT), PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG
    )
    assert "possible, not confirmed" in light_curve.reason


def test_an_unknown_target_position_or_no_star_is_reported() -> None:
    """Without a star of the target's name, both amplitude rows say why."""
    assert (
        "carries the target's name"
        in target_amplitude_inputs([], _TARGET, _CENTER_RA, _CENTER_DEC, None).catalog_reason
    )


def test_vsx_never_asked_is_told_apart_from_vsx_with_no_range() -> None:
    """The catalog row's reason says whether VSX was asked at all."""
    star = _light_curve_star()
    never = StellarObject(id=star.id, name=star.name)
    no_range = StellarObject(id=star.id, name=star.name, vsxVariabilityType="LPV", vsxRangeChecked=True)
    legacy = StellarObject(id=star.id, name=star.name, vsxVariabilityType="LPV")
    assert (
        "never asked"
        in target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([never])).catalog_reason
    )
    assert (
        "no brightness range"
        in target_amplitude_inputs(
            [star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([no_range])
        ).catalog_reason
    )
    assert (
        "saved before VSX ranges were kept; run backfill with --refresh-vsx-ranges"
        in target_amplitude_inputs(
            [star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([legacy])
        ).catalog_reason
    )
    assert (
        "could not be loaded"
        in target_amplitude_inputs([star], _TARGET, _CENTER_RA, _CENTER_DEC, _Catalog([])).catalog_reason
    )


def test_a_partly_covered_fold_stores_the_coverage_and_no_amplitude() -> None:
    """The coverage row is present even when the amplitude is not."""
    inputs = TargetAmplitudeInputs(
        star_id="Algol",
        light_curve=FoldedAmplitude(None, None, 0.5, 120, "the light curve fills 5 of 10 phase bins"),
        light_curve_reason="the light curve fills 5 of 10 phase bins",
        period_days=2.87,
        period_source="the VSX period",
    )
    rows = target_amplitude_measurements(inputs, CONTEXT)
    assert one(rows, PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG).value is None
    assert one(rows, PHOTOMETRY_TARGET_PHASE_COVERAGE_FRACTION).value == pytest.approx(0.5)
