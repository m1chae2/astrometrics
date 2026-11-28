"""Purpose: Unit tests for how the stacking run lines the group stacks up.

Description: An imaging stack is lined up by Siril and a group Siril cannot
solve is left out. A spectral stack uses Siril where it solves and the
zero-order star for the other groups. A stack of the Moon has no stars and is
shifted by correlation. Siril is not started: a stand-in takes the place of the
driver's `register_group_stacks` and returns groups with made-up plate
solutions.
"""

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.siril.stacking.group_registration import RegisteredGroup
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.pipelines.stacking import stack_runner
from astrometricslib.pipelines.stacking.processing import siril_group_alignment
from astrometricslib.pipelines.stacking.processing.siril_group_alignment import describe_registered_group
from astrometricslib.pipelines.stacking.test.test_group_position_map import make_wcs

SIZE = 160


def star_image(row: float, column: float) -> np.ndarray:
    """Draw one bright star on a flat sky.

    Returns
    -------
    image : `numpy.ndarray`
        A ``SIZE`` square float32 image.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    return (1.0 + 100.0 * np.exp(-((rows - row) ** 2 + (columns - column) ** 2) / (2 * 2.0**2))).astype(
        np.float32
    )


def solved_group(image: np.ndarray, column_offset: float = 0.0) -> RegisteredGroup:
    """Make what the driver returns for a stack it solved.

    Parameters
    ----------
    image : `numpy.ndarray`
        The lined-up image the driver returns.
    column_offset : `float`, optional
        How many columns the group's own sky map is shifted from the common
        grid's.

    Returns
    -------
    group : `RegisteredGroup`
        A solved group with made-up plate solutions.
    """
    return RegisteredGroup(
        True,
        None,
        100,
        80,
        0.2,
        3,
        make_wcs(80 + column_offset, 80).to_header(),
        image,
        make_wcs(80, 80).to_header(),
    )


def make_results(images: list[np.ndarray], exposures: list[float]) -> list[stack_runner.GroupResult]:
    """Make stacked groups the way the run holds them.

    Returns
    -------
    results : `list` [`GroupResult`]
        One group for each image.
    """
    return [
        stack_runner.GroupResult(
            group=SimpleNamespace(frames=[]),
            path=f"group_{index}.fits",
            diagnostics={},
            summary_index=index,
            exposure_s=exposure,
            count=10,
            image=image,
        )
        for index, (image, exposure) in enumerate(zip(images, exposures, strict=True))
    ]


def install(monkeypatch: pytest.MonkeyPatch, groups: list[RegisteredGroup]) -> list[Any]:
    """Replace the driver call with one that returns the given groups.

    Returns
    -------
    calls : `list`
        The arguments of each call.
    """
    calls: list[Any] = []

    def register(paths: list[str], reference_index: int, siril_executable: str) -> list[RegisteredGroup]:
        """Return the prepared groups.

        Returns
        -------
        groups : `list` [`RegisteredGroup`]
            The prepared groups.
        """
        calls.append((paths, reference_index))
        return groups

    monkeypatch.setattr(siril_group_alignment, "register_group_stacks", register)
    return calls


@pytest.fixture(autouse=True)
def siril_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the run a Siril command without reading the configuration."""
    monkeypatch.setattr(AppConfiguration, "get_siril_executable", lambda self: "siril")


def test_an_imaging_group_siril_cannot_solve_is_left_out_with_siril_s_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unsolved group has no image, no fallback and Siril's own words."""
    images = [star_image(80, 80), star_image(80, 83), star_image(80, 81)]
    unsolved = RegisteredGroup(False, "Siril found 4 star(s) in the stack and could not match them", 4)
    calls = install(monkeypatch, [solved_group(images[0]), solved_group(images[1], 3.0), unsolved])
    results = make_results(images, [60.0, 30.0, 10.0])

    aligned, covered, alignments = stack_runner._line_up_groups(results, 0, "NGC 1", False)

    assert calls == [(["group_0.fits", "group_1.fits", "group_2.fits"], 0)]
    assert aligned[2] is None
    assert covered[2] is None
    assert alignments[2].failure_reason is not None
    assert "4 star(s)" in alignments[2].failure_reason
    assert "4 star(s)" in stack_runner.alignment_left_out_reason(60.0, alignments[2])
    assert [alignment.method for alignment in alignments[:2]] == ["siril", "siril"]
    assert alignments[0].is_reference
    assert alignments[1].shift_columns_pixels == pytest.approx(-3.0, abs=0.01)
    details = stack_runner._alignment_details(alignments[1])
    assert details["alignment_method"] == "siril"
    assert details["alignment_matched_stars"] == 80
    assert details["alignment_fit_residual_pixels"] is not None
    assert (
        stack_runner._alignment_details(alignments[2])["alignment_failure_reason"]
        == alignments[2].failure_reason
    )


def test_an_imaging_stack_keeps_its_reference_when_siril_cannot_solve_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reference stands alone and every other group is left out."""
    images = [star_image(80, 80), star_image(80, 83)]
    reason = "Siril found 3 star(s) in the stack and could not match them"
    install(monkeypatch, [RegisteredGroup(False, reason, 3), RegisteredGroup(False, reason, 3)])

    aligned, covered, alignments = stack_runner._line_up_groups(
        make_results(images, [60.0, 30.0]), 0, "NGC 1", False
    )

    assert aligned[0] is not None
    assert covered[0] is not None
    assert aligned[1] is None
    assert alignments[0].method == "none"
    assert alignments[0].is_reference


def test_the_moon_is_shifted_by_correlation_without_asking_siril(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body of the solar system has no stars to solve."""
    calls = install(monkeypatch, [])
    reference = np.random.default_rng(2).normal(size=(SIZE, SIZE)).astype(np.float32)
    shifted = np.roll(reference, (3, -2), axis=(0, 1))

    aligned, _, alignments = stack_runner._line_up_groups(
        make_results([reference, shifted], [2.0, 1.0]), 0, "Moon", False
    )

    assert calls == []
    assert alignments[1].method == "correlation"
    assert alignments[1].shift_rows_pixels == pytest.approx(-3.0, abs=0.2)
    assert aligned[1] is not None


def test_a_spectral_group_siril_cannot_solve_is_shifted_by_its_zero_order_star(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Siril's reason is kept, and the zero-order star decides the shift."""
    reference = star_image(80, 80)
    star_poor = star_image(84, 77)
    reason = "Siril found 2 star(s) in the stack and could not match them"
    install(
        monkeypatch,
        [solved_group(reference), solved_group(reference), RegisteredGroup(False, reason, 2)],
    )
    results = make_results([reference, reference, star_poor], [5.0, 4.0, 0.1])

    aligned, _, alignments = stack_runner._line_up_groups(results, 0, "Alnath", True)

    assert [alignment.method for alignment in alignments] == ["siril", "siril", "zero_order"]
    assert alignments[2].shift_rows_pixels == pytest.approx(-4.0, abs=0.3)
    assert alignments[2].shift_columns_pixels == pytest.approx(3.0, abs=0.3)
    assert alignments[2].siril_reason == reason
    assert aligned[2] is not None
    assert stack_runner._alignment_details(alignments[2])["alignment_siril_reason"] == reason


def test_when_only_the_reference_solves_every_spectral_group_uses_the_zero_order_star(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Siril lines nothing up without a second solved stack."""
    reference = star_image(80, 80)
    other = star_image(82, 79)
    install(
        monkeypatch,
        [
            RegisteredGroup(False, "Siril solved the stack but wrote no lined-up image", 100),
            RegisteredGroup(False, "found 2", 2),
        ],
    )

    aligned, _, alignments = stack_runner._line_up_groups(
        make_results([reference, other], [5.0, 0.1]), 0, "Alnath", True
    )

    assert alignments[0].is_reference
    assert alignments[1].method == "zero_order"
    assert aligned[0] is not None
    assert aligned[1] is not None


def test_zero_pixels_inside_a_stack_are_still_data() -> None:
    """A stack clipped at zero is covered except near the edge of its data."""
    image = np.zeros((SIZE, SIZE), dtype=np.float32)
    group = solved_group(image)
    group.solved_header = make_wcs(80, 80).to_header()

    _, covered, result = describe_registered_group(group, True)

    assert covered is not None
    assert covered[80, 80]
    assert covered.mean() > 0.85
    assert result.fit_residual_pixels == pytest.approx(0.2, abs=0.01)


def test_a_solution_with_no_sky_map_is_not_used() -> None:
    """A solved header without a sky map gives no move and a reason."""
    group = solved_group(star_image(80, 80))
    group.solved_header = fits.Header()

    aligned, covered, result = describe_registered_group(group, False)

    assert aligned is None
    assert covered is None
    assert result.failure_reason is not None
