"""Purpose: Regression tests for session WCS resolution and star ID.

Description: Verifies resolve_frame_wcs() reuses an existing FITS-header WCS
without ever plate-solving, falls back to solving when no usable header WCS
exists, and never solves when allow_solve is False. Verifies
identify_session_stars() detects sources, resolves a WCS, and runs full
SIMBAD identification against every detected star.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from astrometricslib.tasks.stellar_tasks.astrometry_tasks import session_identification
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.session_identification import (
    identify_session_stars,
    resolve_frame_wcs,
)
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier


def _make_star_identifier() -> StarIdentifier:
    config = MagicMock()
    config.get_value.return_value = None
    config.get_focal_length_mm.return_value = None
    return StarIdentifier(config=config)


def _make_fake_image(wcs=None, path="/fake/session_reference.fits"):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    image = MagicMock()
    image.wcs = wcs
    image.path = path
    image.data = np.zeros((100, 100), dtype=np.float32)
    image.header = {}
    return image


def _make_celestial_wcs():  # ruff: ignore[missing-return-type-private-function]
    wcs = MagicMock()
    wcs.is_celestial = True
    return wcs


class TestResolveFrameWcs:
    """Unit test suite for resolve_frame_wcs."""

    def test_reuses_existing_header_wcs_without_solving(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
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

    def test_non_celestial_header_wcs_is_not_reused(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
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

    def test_solves_when_no_header_wcs_present(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify solver is called when header WCS is missing."""
        identifier = _make_star_identifier()
        fake_header = MagicMock()
        identifier.solver.solve = MagicMock(return_value=fake_header)
        image = _make_fake_image(wcs=None)

        constructed_wcs = _make_celestial_wcs()
        monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=constructed_wcs))
        write_back_spy = MagicMock()
        monkeypatch.setattr(session_identification, "_write_wcs_to_header", write_back_spy)

        wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert wcs is constructed_wcs
        assert reused is False
        assert solve_attempted is True
        identifier.solver.solve.assert_called_once()
        write_back_spy.assert_called_once_with(image.path, constructed_wcs)

    def test_write_back_skipped_when_disabled(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify header write-back is skipped when disabled."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=MagicMock())
        image = _make_fake_image(wcs=None)

        monkeypatch.setattr(session_identification, "WCS", MagicMock(return_value=_make_celestial_wcs()))
        write_back_spy = MagicMock()
        monkeypatch.setattr(session_identification, "_write_wcs_to_header", write_back_spy)

        resolve_frame_wcs(image, identifier, write_back=False)

        write_back_spy.assert_not_called()

    def test_solve_failure_returns_none(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify failed plate solve returns None WCS."""
        identifier = _make_star_identifier()
        identifier.solver.solve = MagicMock(return_value=None)
        image = _make_fake_image(wcs=None)

        wcs, reused, solve_attempted = resolve_frame_wcs(image, identifier)

        assert wcs is None
        assert reused is False
        assert solve_attempted is True

    def test_allow_solve_false_never_solves(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
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

    def _fake_sources(self, count):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return [
            {"xcentroid": float(i * 10), "ycentroid": float(i * 10), "flux": 1000.0 - i} for i in range(count)
        ]

    def test_reuses_header_wcs_and_identifies_every_detected_star(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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

    def test_caps_detections_at_max_detections(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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

    def test_no_wcs_available_skips_identification(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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

    def test_simbad_matched_count_reflects_identified_stars(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify SIMBAD matched count matches identified stars."""
        identifier = _make_star_identifier()
        image = _make_fake_image(wcs=_make_celestial_wcs())

        sources = self._fake_sources(3)
        identifier.detector.detect = MagicMock(return_value=sources)
        identifier.detector.deduplicate = MagicMock(return_value=sources)

        def _fake_identify(stellar_objects, wcs, width, height):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            stellar_objects[0].spectral_type = "A0Va"
            return stellar_objects

        monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_fake_identify))

        result = identify_session_stars(image, identifier)

        assert result.simbad_matched_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
