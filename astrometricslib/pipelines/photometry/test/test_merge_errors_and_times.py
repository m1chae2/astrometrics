"""Purpose: Test that merging sessions keeps flux errors and BJD_TDB times.

Description: A star seen on two nights gets one light curve made by
`_merge_light_curves`. The per-measurement errors and BJD_TDB times must be
joined and sorted the same way as the fluxes, and an array that only one
night has must be dropped, because pairing half an array with the merged
timestamps would be a guess. The tests also check the `PhotometryResult`
field names that the user interface reads.
"""

from datetime import datetime, timedelta

import pytest

from astrometricslib.models.stellar_source import PhotometryResult
from astrometricslib.pipelines.photometry.batch import _field_position_deg, _merge_light_curves
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    TIME_BASIS_BJD_TDB,
    TIME_BASIS_BJD_TDB_GEOCENTRIC,
)

FIRST_NIGHT = datetime(2026, 5, 24, 4, 0, 0)
SECOND_NIGHT = datetime(2026, 5, 25, 4, 0, 0)


def _night(
    start: datetime,
    count: int,
    *,
    with_errors: bool = True,
    with_times: bool = True,
    unit_gain: bool = True,
    basis: str = TIME_BASIS_BJD_TDB,
) -> PhotometryResult:
    """Build one night's light curve.

    Parameters
    ----------
    start : `datetime.datetime`
        The first exposure start.
    count : `int`
        The number of frames, a minute apart.
    with_errors : `bool`, optional
        Fill the error arrays. The flux error of frame ``i`` is ``i / 10``
        plus the night's offset, so each value says where it came from.
    with_times : `bool`, optional
        Fill `time_bjd_tdb`.
    unit_gain : `bool`, optional
        The value of `errors_assume_unit_gain` when errors are present.
    basis : `str`, optional
        The `time_basis` when times are present.

    Returns
    -------
    light_curve : `PhotometryResult`
        The night's light curve.
    """
    offset = (start - FIRST_NIGHT).days
    stamps = [start + timedelta(minutes=minute) for minute in range(count)]
    errors = [offset + 0.1 * minute for minute in range(count)]
    return PhotometryResult(
        timestamps=stamps,
        fluxes=[100.0 + offset] * count,
        fluxes_normalized=[1.0 + offset] * count,
        fluxes_detrended=[1.0 + offset] * count,
        flux_errors=errors if with_errors else [],
        fluxes_normalized_errors=[error / 100.0 for error in errors] if with_errors else [],
        fluxes_detrended_errors=[error / 100.0 for error in errors] if with_errors else [],
        errors_assume_unit_gain=unit_gain if with_errors else None,
        errors_assume_zero_read_noise=True if with_errors else None,
        time_bjd_tdb=[2461184.5 + offset + minute / 1440.0 for minute in range(count)] if with_times else [],
        time_basis=basis if with_times else None,
    )


def test_errors_and_times_are_joined_and_sorted_with_the_fluxes() -> None:
    """The later night goes first; the merge still orders by time."""
    merged = _merge_light_curves(_night(SECOND_NIGHT, 3), _night(FIRST_NIGHT, 4))

    assert merged.timestamps == sorted(merged.timestamps)
    assert merged.flux_errors == pytest.approx([0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2])
    assert merged.fluxes_normalized_errors == pytest.approx([e / 100.0 for e in merged.flux_errors])
    assert merged.fluxes_detrended_errors == pytest.approx(merged.fluxes_normalized_errors)
    assert merged.time_bjd_tdb == sorted(merged.time_bjd_tdb)
    assert len(merged.time_bjd_tdb) == 7
    assert merged.time_basis == TIME_BASIS_BJD_TDB
    assert merged.errors_assume_unit_gain is True


def test_an_array_that_only_one_night_has_is_dropped() -> None:
    """Errors from one night cannot be paired with the other night's frames."""
    merged = _merge_light_curves(_night(FIRST_NIGHT, 4), _night(SECOND_NIGHT, 3, with_errors=False))

    assert merged.flux_errors == []
    assert merged.fluxes_normalized_errors == []
    assert merged.fluxes_detrended_errors == []
    assert merged.errors_assume_unit_gain is None
    assert len(merged.timestamps) == 7


def test_times_that_only_one_night_has_are_dropped_with_their_basis() -> None:
    """A half-filled BJD_TDB list is not kept, and the basis goes with it."""
    merged = _merge_light_curves(_night(FIRST_NIGHT, 4), _night(SECOND_NIGHT, 3, with_times=False))

    assert merged.time_bjd_tdb == []
    assert merged.time_basis is None


def test_the_merged_flags_are_true_if_either_night_assumed() -> None:
    """A night with a known gain does not clear the other's assumption."""
    merged = _merge_light_curves(
        _night(FIRST_NIGHT, 3, unit_gain=False), _night(SECOND_NIGHT, 3, unit_gain=True)
    )

    assert merged.errors_assume_unit_gain is True


def test_nights_with_different_time_bases_merge_to_the_less_exact_one() -> None:
    """A site-corrected night merged with a geocentric one reads geocentric."""
    merged = _merge_light_curves(
        _night(FIRST_NIGHT, 3), _night(SECOND_NIGHT, 3, basis=TIME_BASIS_BJD_TDB_GEOCENTRIC)
    )

    assert merged.time_basis == TIME_BASIS_BJD_TDB_GEOCENTRIC
    assert (
        _merge_light_curves(_night(FIRST_NIGHT, 3), _night(SECOND_NIGHT, 3)).time_basis == TIME_BASIS_BJD_TDB
    )


def test_the_new_fields_use_the_camel_case_names_the_interface_reads() -> None:
    """The model reads and writes the aliases used in the generated types."""
    light_curve = PhotometryResult.model_validate({
        "fluxErrors": [0.5],
        "fluxesNormalizedErrors": [0.01],
        "fluxesDetrendedErrors": [0.02],
        "errorsAssumeUnitGain": True,
        "errorsAssumeZeroReadNoise": False,
        "timeBjdTdb": [2461184.5],
        "timeBasis": TIME_BASIS_BJD_TDB,
    })

    dumped = light_curve.model_dump(by_alias=True)

    assert light_curve.flux_errors == [0.5]
    assert light_curve.time_bjd_tdb == [2461184.5]
    assert dumped["errorsAssumeUnitGain"] is True
    assert dumped["timeBasis"] == TIME_BASIS_BJD_TDB


def test_a_light_curve_saved_before_these_fields_still_loads_with_them_empty() -> None:
    """Old rows have no errors or BJD_TDB times; both stay empty."""
    light_curve = PhotometryResult.model_validate({"fluxes": [1.0, 2.0]})

    assert light_curve.flux_errors == []
    assert light_curve.fluxes_normalized_errors == []
    assert light_curve.fluxes_detrended_errors == []
    assert light_curve.time_bjd_tdb == []
    assert light_curve.errors_assume_unit_gain is None
    assert light_curve.time_basis is None


class _FakeTarget:
    """A stand-in target with a right ascension and declination."""

    def __init__(self, ra: str, dec: str) -> None:
        """Store the coordinates.

        Parameters
        ----------
        ra, dec : `str`
            The target's right ascension and declination.
        """
        self.ra = ra
        self.dec = dec


def test_the_field_position_is_the_targets_own_position_when_it_has_one() -> None:
    """A target at 16h41m +36d28m gives about (250.25, 36.47) degrees."""
    position = _field_position_deg(_FakeTarget("16h 41m 00s", "+36d 28m 00s"), None)

    assert position == pytest.approx((250.25, 36.4667), abs=1e-3)


def test_the_field_position_falls_back_to_the_plate_solution_then_to_none() -> None:
    """With the placeholder position, the plate solution gives the position."""
    from astropy.wcs import WCS

    wcs = WCS(naxis=2)
    wcs.wcs.crval = [83.8, -5.4]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    class _Solved:
        """A stand-in star-lookup result carrying a plate solution."""

    solved = _Solved()
    solved.wcs = wcs
    placeholder = _FakeTarget("0h 0m 0s", "0d 0m 0s")

    assert _field_position_deg(placeholder, solved) == pytest.approx((83.8, -5.4))
    assert _field_position_deg(placeholder, None) is None
    assert _field_position_deg(None, None) is None
