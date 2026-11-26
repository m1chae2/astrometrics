"""Purpose: Regression tests for session WCS resolution and star ID.

Description: Verifies resolve_frame_wcs() reuses an existing FITS-header WCS
without ever plate-solving, falls back to solving when no usable header WCS
exists, and never solves when allow_solve is False. Verifies
identify_session_stars() detects sources, resolves a WCS, and runs full
SIMBAD identification against every detected star.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.shared import session_identification
from astrometricslib.pipelines.shared.session_identification import (
    identify_session_stars,
    resolve_frame_wcs,
)


def _make_star_identifier() -> StarIdentifier:
    config = MagicMock()
    config.get_value.return_value = None
    config.get_focal_length_mm.return_value = None
    return StarIdentifier(config=config)


def _make_fake_image(wcs=None, path="/fake/session_reference.fits") -> MagicMock:  # ruff: ignore[missing-type-function-argument]
    image = MagicMock()
    image.wcs = wcs
    image.path = path
    image.data = np.zeros((100, 100), dtype=np.float32)
    # A plate-solved header, so a header map counts as a solution.
    image.header = {
        "PLTSOLVD": True,
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": 10.0,
        "CRVAL2": 41.0,
        "CRPIX1": 50.0,
        "CRPIX2": 50.0,
    }
    return image


def _make_celestial_wcs() -> MagicMock:
    wcs = MagicMock()
    wcs.is_celestial = True
    return wcs


class TestResolveFrameWcs:
    """Unit test suite for resolve_frame_wcs."""

    def test_reuses_existing_header_wcs_without_solving(self) -> None:
        """Verify header WCS is reused without plate-solving."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)

        wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert wcs is header_wcs
        assert reused is True
        assert solve_attempted is False
        identifier.solver.solve.assert_not_called()

    def test_a_pointing_map_that_is_not_plate_solved_is_not_reused(self) -> None:
        """Verify a map INDI wrote from the pointing is solved fresh."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=None)
        image = _make_fake_image(wcs=_make_celestial_wcs())
        image.header = {"CTYPE1": "RA---TAN", "CRVAL1": 10.7}

        _wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert reused is False
        assert solve_attempted is True

    def test_a_map_the_caller_trusts_is_reused_without_the_plate_solved_card(self) -> None:
        """Verify a borrowed stack map is reused without PLTSOLVD."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock()
        borrowed = _make_celestial_wcs()
        image = _make_fake_image(wcs=borrowed)
        image.header = {}

        wcs, reused, _solve_attempted = resolve_frame_wcs(image, identifier, trust_image_wcs=True)

        assert wcs is borrowed
        assert reused is True
        identifier.solver.solve.assert_not_called()

    def test_non_celestial_header_wcs_is_not_reused(self) -> None:
        """Verify non-celestial header WCS triggers plate-solving."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=None)
        non_celestial_wcs = MagicMock()
        non_celestial_wcs.is_celestial = False
        image = _make_fake_image(wcs=non_celestial_wcs)

        _wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert reused is False
        assert solve_attempted is True
        identifier.solver.solve.assert_called_once()

    def test_solves_when_no_header_wcs_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify solver is called when header WCS is missing."""
        identifier = _make_star_identifier()
        fake_header = MagicMock()
        identifier.solver.solve = MagicMock(return_value=fake_header)
        image = _make_fake_image(wcs=None)

        constructed_wcs = _make_celestial_wcs()
        monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=constructed_wcs))
        write_back_spy = MagicMock()
        monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", write_back_spy)

        wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert wcs is constructed_wcs
        assert reused is False
        assert solve_attempted is True
        identifier.solver.solve.assert_called_once()
        write_back_spy.assert_called_once_with(image.path, constructed_wcs)

    def test_write_back_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify header write-back is skipped when disabled."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=MagicMock())
        image = _make_fake_image(wcs=None)

        monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=_make_celestial_wcs()))
        write_back_spy = MagicMock()
        monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", write_back_spy)

        resolve_frame_wcs(image, identifier, write_back=False)

        write_back_spy.assert_not_called()

    def test_solve_failure_returns_none(self) -> None:
        """Verify failed plate solve returns None WCS."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=None)
        image = _make_fake_image(wcs=None)

        wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert wcs is None
        assert reused is False
        assert solve_attempted is True

    def test_allow_solve_false_never_solves(self) -> None:
        """Verify allow_solve=False prevents solving attempts."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock()
        image = _make_fake_image(wcs=None)

        wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier, allow_solve=False)

        assert wcs is None
        assert reused is False
        assert solve_attempted is False
        identifier.solver.solve.assert_not_called()


class TestIdentifySessionStars:
    """Unit test suite for identify_session_stars."""

    def _fake_sources(self, count):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
        return [
            {"xcentroid": float(i * 10), "ycentroid": float(i * 10), "flux": 1000.0 - i} for i in range(count)
        ]

    def test_reuses_header_wcs_and_identifies_every_detected_star(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify all detected stars are identified with header WCS."""
        identifier = _make_star_identifier()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)

        sources = self._fake_sources(3)
        identifier.detector.detect = MagicMock(return_value=sources)
        identifier.detector.deduplicate = MagicMock(return_value=sources)

        identify_spy = MagicMock(side_effect=lambda stellar_objects, wcs, width, height: stellar_objects)
        monkeypatch.setattr(identifier, "identify_stars_with_wcs", identify_spy)

        result = identify_session_stars(image, identifier)

        assert result.wcs is header_wcs
        assert result.reused_existing_header_wcs is True
        assert result.plate_solve_succeeded is True
        assert result.sources_detected == 3
        assert len(result.stellar_objects) == 3

        identify_spy.assert_called_once()
        called_stellar_objects = identify_spy.call_args.args[0]
        assert len(called_stellar_objects) == 3
        assert called_stellar_objects is result.stellar_objects

    def test_caps_detections_at_max_detections(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify detections are capped at max_detections parameter."""
        identifier = _make_star_identifier()
        image = _make_fake_image(wcs=_make_celestial_wcs())

        sources = self._fake_sources(10)
        identifier.detector.detect = MagicMock(return_value=sources)
        identifier.detector.deduplicate = MagicMock(return_value=sources)
        monkeypatch.setattr(
            identifier, "identify_stars_with_wcs", MagicMock(side_effect=lambda s, w, wi, h: s)
        )

        result = identify_session_stars(image, identifier, max_detections=5)

        assert result.sources_detected == 10
        assert len(result.stellar_objects) == 5

    def test_no_wcs_available_skips_identification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify SIMBAD lookup is skipped when no WCS is available."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=None)
        image = _make_fake_image(wcs=None)

        sources = self._fake_sources(2)
        identifier.detector.detect = MagicMock(return_value=sources)
        identifier.detector.deduplicate = MagicMock(return_value=sources)
        identify_spy = MagicMock()
        monkeypatch.setattr(identifier, "identify_stars_with_wcs", identify_spy)

        result = identify_session_stars(image, identifier)

        assert result.wcs is None
        assert result.plate_solve_succeeded is False
        assert len(result.stellar_objects) == 2
        identify_spy.assert_not_called()

    def test_simbad_matched_count_reflects_identified_stars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify SIMBAD matched count matches identified stars."""
        identifier = _make_star_identifier()
        image = _make_fake_image(wcs=_make_celestial_wcs())

        sources = self._fake_sources(3)
        identifier.detector.detect = MagicMock(return_value=sources)
        identifier.detector.deduplicate = MagicMock(return_value=sources)

        def _fake_identify(stellar_objects, wcs, width, height):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
            stellar_objects[0].spectral_type = "A0Va"
            return stellar_objects

        monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_fake_identify))

        result = identify_session_stars(image, identifier)

        assert result.simbad_matched_count == 1


class TestReusedHeaderWcsVerification:
    """Verify a reused header WCS is checked against catalog matches.

    `resolve_frame_wcs`'s reuse gate is `is_celestial`, a purely
    structural check -- an astrometrically inaccurate header WCS passes
    it, then projects stars outside the 10 arcsec SIMBAD/Gaia match
    radius so almost nothing identifies. These tests cover the
    verification that catches that and re-solves.
    """

    def _sources(self, count):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
        return [{"xcentroid": float(i), "ycentroid": float(i), "flux": 1000.0 - i} for i in range(count)]

    def _identify_marking(self, matched_count) -> Callable[..., Any]:  # ruff: ignore[missing-type-function-argument]
        """Build an identify_stars_with_wcs stand-in marking N as matched.

        Returns
        -------
        identify : callable
            A stand-in for `identify_stars_with_wcs`.
        """

        def _identify(stellar_objects, wcs, width, height):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
            # The real identifier gives every star a sky position: a
            # catalog star's, or the projected one for a position-only star.
            for index, star in enumerate(stellar_objects):
                star.right_ascension = 10.0 + index * 0.01
                star.declination = 41.0
            for star in stellar_objects[:matched_count]:
                star.is_catalog_identified = True
            return stellar_objects

        return _identify

    def _prepare(self, identifier, count) -> None:  # ruff: ignore[missing-type-function-argument]
        sources = self._sources(count)
        identifier.detector.detect = MagicMock(return_value=sources)
        identifier.detector.deduplicate = MagicMock(return_value=sources)

    def test_good_match_rate_keeps_header_wcs_without_solving(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A header WCS matching plenty of catalog stars is left alone."""
        identifier = _make_star_identifier()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)
        self._prepare(identifier, 40)
        # 20/40 = 50%, far above the 10% floor.
        monkeypatch.setattr(
            identifier, "identify_stars_with_wcs", MagicMock(side_effect=self._identify_marking(20))
        )
        identifier.solver.solve = MagicMock()

        result = identify_session_stars(image, identifier)

        assert result.wcs is header_wcs
        assert result.reused_existing_header_wcs is True
        assert result.header_wcs_replaced_after_verification is False
        identifier.solver.solve.assert_not_called()

    def test_poor_match_rate_triggers_resolve_and_adopts_better_wcs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A header WCS matching almost nothing is replaced by a solve."""
        identifier = _make_star_identifier()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)
        self._prepare(identifier, 40)

        fresh_wcs = _make_celestial_wcs()
        identifier.solver.solve = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=fresh_wcs))
        write_back_spy = MagicMock()
        monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", write_back_spy)

        # First pass (header WCS) matches 1/40 = 2.5%; the re-solve matches 25.
        marks = iter([self._identify_marking(1), self._identify_marking(25)])
        monkeypatch.setattr(
            identifier,
            "identify_stars_with_wcs",
            MagicMock(side_effect=lambda *a, **k: next(marks)(*a, **k)),
        )

        result = identify_session_stars(image, identifier)

        assert result.wcs is fresh_wcs
        assert result.reused_existing_header_wcs is False
        assert result.header_wcs_replaced_after_verification is True
        assert result.solve_attempted is True
        assert sum(1 for s in result.stellar_objects if s.is_catalog_identified) == 25
        write_back_spy.assert_called_once()

    def test_keeps_header_wcs_when_resolve_is_not_better(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A re-solve that doesn't improve matches is discarded, header kept.

        The header must not be overwritten in this case -- a worse
        solve replacing a bad-but-existing solution helps nobody.
        """
        identifier = _make_star_identifier()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)
        self._prepare(identifier, 40)

        identifier.solver.solve = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=_make_celestial_wcs()))
        write_back_spy = MagicMock()
        monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", write_back_spy)

        marks = iter([self._identify_marking(2), self._identify_marking(1)])
        monkeypatch.setattr(
            identifier,
            "identify_stars_with_wcs",
            MagicMock(side_effect=lambda *a, **k: next(marks)(*a, **k)),
        )

        result = identify_session_stars(image, identifier)

        assert result.wcs is header_wcs
        assert result.reused_existing_header_wcs is True
        assert result.header_wcs_replaced_after_verification is False
        write_back_spy.assert_not_called()

    def test_keeps_header_wcs_when_resolve_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed re-solve leaves the header WCS in place."""
        identifier = _make_star_identifier()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)
        self._prepare(identifier, 40)

        identifier.solver.solve = MagicMock(return_value=None)
        monkeypatch.setattr(
            identifier, "identify_stars_with_wcs", MagicMock(side_effect=self._identify_marking(1))
        )

        result = identify_session_stars(image, identifier)

        assert result.wcs is header_wcs
        assert result.reused_existing_header_wcs is True
        assert result.header_wcs_replaced_after_verification is False

    def test_sparse_field_is_not_verified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Too few stars to judge by means no re-solve is attempted."""
        identifier = _make_star_identifier()
        header_wcs = _make_celestial_wcs()
        image = _make_fake_image(wcs=header_wcs)
        # Below MIN_STARS_TO_VERIFY_REUSED_WCS, so a 0% match rate is
        # treated as uninformative rather than as a broken WCS.
        self._prepare(identifier, session_identification.MIN_STARS_TO_VERIFY_REUSED_WCS.default - 1)
        identifier.solver.solve = MagicMock()
        monkeypatch.setattr(
            identifier, "identify_stars_with_wcs", MagicMock(side_effect=self._identify_marking(0))
        )

        result = identify_session_stars(image, identifier)

        assert result.wcs is header_wcs
        assert result.header_wcs_replaced_after_verification is False
        identifier.solver.solve.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_a_written_sky_map_is_marked_as_plate_solved(tmp_path: Path) -> None:
    """Writing a sky map adds the plate-solve card."""
    from astropy.io import fits
    from astropy.wcs import WCS

    from astrometricslib._internal.drivers.fits_access import header_is_plate_solved
    from astrometricslib.pipelines.shared.session_identification import write_wcs_to_fits_header

    path = tmp_path / "frame.fits"
    header = fits.Header()
    header["CRVAL1"] = 10.0
    header["CRVAL2"] = 20.0
    fits.PrimaryHDU(data=np.zeros((10, 10), dtype=np.float32), header=header).writeto(path)
    assert not header_is_plate_solved(fits.getheader(path))

    solved = WCS(naxis=2)
    solved.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    solved.wcs.crval = [11.0, 21.0]
    solved.wcs.crpix = [5.0, 5.0]
    solved.wcs.cdelt = [-0.001, 0.001]
    write_wcs_to_fits_header(str(path), solved)

    assert header_is_plate_solved(fits.getheader(path))
    assert fits.getheader(path)["CRVAL1"] == pytest.approx(11.0)


# Plate scales, in arcseconds per pixel, of the two setups the wrong-solution
# check must work on: the ZWO ASI533MM Pro (3.76 micron pixels) on the 405 mm
# refractor, and the Nikon D5300 (3.89 micron pixels) behind a 300 mm lens.
# The catalog match radius follows the plate scale, so it is 10 arcsec on the
# first and about 14 arcsec on the second.
_ASI533_PLATE_SCALE = 206.265 * 3.76 / 405.0
_D5300_300MM_PLATE_SCALE = 206.265 * 3.89 / 300.0


def _stars_with_offsets(offsets_arcsec: list[float], unmatched: int) -> list:
    """Build identified stars, matched ones at the given catalog distances.

    Parameters
    ----------
    offsets_arcsec : `list` [`float`]
        One catalog match distance per matched star.
    unmatched : `int`
        How many more stars have no catalog match.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The matched stars first, then the unmatched ones.
    """
    from astrometricslib.models.quality.astrometry_quality import CatalogMatchQuality
    from astrometricslib.models.stellar_source import StellarObject

    stars = []
    for index, offset in enumerate(offsets_arcsec):
        star = StellarObject(
            id=f"Gaia DR3 {index + 1}", right_ascension=10.0 + index * 0.01, declination=41.0
        )
        star.is_catalog_identified = True
        star.catalog_match_quality = CatalogMatchQuality(matched_via="gaia", separation_arcsec=offset)
        stars.append(star)
    for index in range(unmatched):
        stars.append(
            StellarObject(id=f"FIELD_J{index}", right_ascension=11.0 + index * 0.01, declination=41.0)
        )
    return stars


def _chance_match_offsets(count: int, radius_arcsec: float, seed: int) -> list[float]:
    """Draw chance-match distances spread evenly over the match circle.

    A wrong solution pairs a star with whatever catalog star lies within the
    match radius. Such a star is equally likely to be anywhere in the circle,
    so its distance is the radius times the square root of an even draw.

    Parameters
    ----------
    count : `int`
        How many matches.
    radius_arcsec : `float`
        The match radius.
    seed : `int`
        The random seed.

    Returns
    -------
    offsets : `list` [`float`]
        The match distances, in arcseconds.
    """
    generator = np.random.default_rng(seed)
    return (radius_arcsec * np.sqrt(generator.uniform(0.0, 1.0, count))).tolist()


@pytest.mark.parametrize("plate_scale", [_ASI533_PLATE_SCALE, _D5300_300MM_PLATE_SCALE])
class TestPlateSolutionProblem:
    """Verify the wrong-solution check catches chance matches, both scales."""

    def test_a_correct_solution_passes(self, plate_scale: float) -> None:
        """Matches within a couple of arcseconds of their catalog stars pass.

        Parameters
        ----------
        plate_scale : `float`
            The run's plate scale, in arcseconds per pixel.
        """
        from astrometricslib.foundation.parameter_context import parameter_context, record_basis
        from astrometricslib.models.tuned_parameter import RunBasis
        from astrometricslib.pipelines.shared.session_identification import plate_solution_problem

        offsets = (np.random.default_rng(1).rayleigh(1.0, 30)).tolist()
        with parameter_context():
            record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, plate_scale)
            assert plate_solution_problem(_stars_with_offsets(offsets, unmatched=20)) is None

    @pytest.mark.parametrize("seed", range(5))
    def test_a_shifted_solution_with_chance_matches_fails(self, plate_scale: float, seed: int) -> None:
        """Chance matches spread over the match circle fail the median test.

        The share of matched stars (25 of 120, about 21%) passes the
        match-share test, as chance matches can in a dense field, so only
        the median distance can catch the wrong solution.

        Parameters
        ----------
        plate_scale : `float`
            The run's plate scale, in arcseconds per pixel.
        seed : `int`
            The random seed of the chance-match distances.
        """
        from astrometricslib.foundation.parameter_context import parameter_context, record_basis
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.models.tuned_parameter import RunBasis
        from astrometricslib.pipelines.shared.catalog_star_identity import CATALOG_MATCH_RADIUS_ARCSEC
        from astrometricslib.pipelines.shared.session_identification import plate_solution_problem

        with parameter_context():
            record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, plate_scale)
            radius = float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value)
            offsets = _chance_match_offsets(25, radius, seed)
            assert max(offsets) <= radius
            problem = plate_solution_problem(_stars_with_offsets(offsets, unmatched=95))
        assert problem is not None
        assert "median" in problem

    def test_the_limit_follows_the_match_radius(self, plate_scale: float) -> None:
        """The limit is half the run's match radius, not a fixed distance.

        Parameters
        ----------
        plate_scale : `float`
            The run's plate scale, in arcseconds per pixel.
        """
        from astrometricslib.foundation.parameter_context import parameter_context, record_basis
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.models.tuned_parameter import RunBasis
        from astrometricslib.pipelines.shared.catalog_star_identity import CATALOG_MATCH_RADIUS_ARCSEC
        from astrometricslib.pipelines.shared.session_identification import plate_solution_problem

        with parameter_context():
            record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, plate_scale)
            limit = 0.5 * float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value)
            assert plate_solution_problem(_stars_with_offsets([0.95 * limit] * 30, unmatched=0)) is None
            assert plate_solution_problem(_stars_with_offsets([1.05 * limit] * 30, unmatched=0)) is not None


def test_a_reused_header_map_with_far_matches_is_solved_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reused map whose matches sit far from their catalog stars is replaced.

    Half the stars match, which passes the match-share test, but every
    match sits 8 arcsec off, past half the 10 arcsec match radius.
    """
    identifier = _make_star_identifier()
    header_wcs = _make_celestial_wcs()
    image = _make_fake_image(wcs=header_wcs)
    sources = [{"xcentroid": float(i), "ycentroid": float(i), "flux": 1000.0 - i} for i in range(40)]
    identifier.detector.detect = MagicMock(return_value=sources)
    identifier.detector.deduplicate = MagicMock(return_value=sources)
    fresh_wcs = _make_celestial_wcs()
    identifier.solver.solve = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=fresh_wcs))
    monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", MagicMock())

    def mark(offset_arcsec: float, count: int) -> Callable[..., Any]:
        """Build an identify stand-in matching ``count`` stars at a distance.

        Returns
        -------
        identify : callable
            A stand-in for `identify_stars_with_wcs`.
        """
        from astrometricslib.models.quality.astrometry_quality import CatalogMatchQuality

        def identify(stellar_objects: list, _wcs: object, _width: int, _height: int) -> list:
            """Mark the first stars as matched and give every star a position.

            Returns
            -------
            stars : `list`
                The same stars.
            """
            for index, star in enumerate(stellar_objects):
                star.right_ascension = 10.0 + index * 0.01
                star.declination = 41.0
            for star in stellar_objects[:count]:
                star.is_catalog_identified = True
                star.catalog_match_quality = CatalogMatchQuality(
                    matched_via="gaia", separation_arcsec=offset_arcsec
                )
            return stellar_objects

        return identify

    marks = iter([mark(8.0, 20), mark(1.0, 30)])
    monkeypatch.setattr(
        identifier, "identify_stars_with_wcs", MagicMock(side_effect=lambda *a, **k: next(marks)(*a, **k))
    )

    result = identify_session_stars(image, identifier)

    assert result.wcs is fresh_wcs
    assert result.header_wcs_replaced_after_verification is True
