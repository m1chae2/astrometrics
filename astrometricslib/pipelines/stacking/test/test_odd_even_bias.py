"""Tests for the odd–even difference's bias measurement by injection (D16).

The Siril stacks themselves are not run here. The pieces that make the
simulated frames, work out the noise to inject and combine the repeats are
tested on small synthetic files.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.stacking.odd_even_bias import (
    KEEP_RADIUS_PX,
    BiasMeasurement,
    BiasRepeat,
    injected_noise_sigma_adu,
    simulate_frame,
    smoothed_sky_frame,
    summarize_repeats,
)

SIZE = 300
STAR = (150, 140)


def _raw_frame(path: Path, *, bayer: str = "", dtype: type = np.uint16, noise: float = 6.0) -> np.ndarray:
    """Write a raw-like frame with sky, noise, a hot pixel and a star.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write.
    bayer : `str`, optional
        A Bayer pattern name for the header, or empty for a mono frame.
    dtype : `type`, optional
        The pixel type.
    noise : `float`, optional
        The real noise's standard deviation.

    Returns
    -------
    data : `numpy.ndarray`
        The pixels written.
    """
    generator = np.random.default_rng(11)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = 1000.0 + generator.normal(0.0, noise, (SIZE, SIZE))
    image += 3000.0 * np.exp(-((columns - STAR[0]) ** 2 + (rows - STAR[1]) ** 2) / (2 * 2.0**2))
    image[40, 40] += 800.0  # a hot pixel
    data = np.rint(image).astype(dtype)
    header = fits.Header()
    header["EXPTIME"] = 120.0
    header["INSTRUME"] = "Test camera"
    if bayer:
        header["BAYERPAT"] = bayer
    fits.PrimaryHDU(data, header=header).writeto(path, overwrite=True)
    return data


def test_the_sigma_to_inject_is_the_frames_predicted_noise() -> None:
    """Sky electrons and read noise squared add; the gain gives ADU."""
    assert injected_noise_sigma_adu(100.0, 3.0, 0.5) == pytest.approx(math.sqrt(109.0) / 0.5)


def test_the_simulated_sky_has_the_injected_noise_and_the_stars_stay(tmp_path: Path) -> None:
    """Sky noise is the chosen sigma; the star and header are real."""
    real = tmp_path / "real.fits"
    data = _raw_frame(real)
    output = tmp_path / "out" / "sim.fits"

    simulate_frame(str(real), str(output), 20.0, np.random.default_rng(1))

    with fits.open(output) as hdul:
        simulated = np.array(hdul[0].data)
        header = hdul[0].header
    assert simulated.dtype == np.uint16
    assert header["EXPTIME"] == pytest.approx(120.0) and header["INSTRUME"] == "Test camera"
    sky = simulated[:100, 200:].astype(float)  # far from the star and the hot pixel
    assert sky.std() == pytest.approx(20.0, rel=0.1)
    assert sky.mean() == pytest.approx(1000.0, abs=2.0)
    # The star core is the real one plus noise, so its peak is still there.
    peak_window = simulated[STAR[1] - 2 : STAR[1] + 3, STAR[0] - 2 : STAR[0] + 3].astype(float)
    real_window = data[STAR[1] - 2 : STAR[1] + 3, STAR[0] - 2 : STAR[0] + 3].astype(float)
    assert np.mean(peak_window - real_window) == pytest.approx(0.0, abs=25.0)
    assert simulated[STAR[1], STAR[0]] > 3000
    # The hot pixel is flagged and so kept.
    assert simulated[40, 40] > 1500


def test_two_seeds_give_different_noise_and_one_seed_repeats(tmp_path: Path) -> None:
    """The seed decides the noise, so a run repeats exactly."""
    real = tmp_path / "real.fits"
    _raw_frame(real)

    def run(seed: int, name: str) -> np.ndarray:
        """Simulate with one seed and read the result.

        Parameters
        ----------
        seed : `int`
            The seed.
        name : `str`
            The output file name.

        Returns
        -------
        data : `numpy.ndarray`
            The simulated pixels.
        """
        output = tmp_path / name
        simulate_frame(str(real), str(output), 20.0, np.random.default_rng(seed))
        return np.array(fits.getdata(output))

    assert np.array_equal(run(5, "a.fits"), run(5, "b.fits"))
    assert not np.array_equal(run(5, "c.fits"), run(6, "d.fits"))


def test_values_are_clipped_to_the_integer_range(tmp_path: Path) -> None:
    """A noisy dark sky cannot wrap around below zero."""
    real = tmp_path / "real.fits"
    generator = np.random.default_rng(2)
    fits.PrimaryHDU(
        np.rint(5 + generator.normal(0, 1, (SIZE, SIZE))).clip(0, 65535).astype(np.uint16)
    ).writeto(real)
    output = tmp_path / "sim.fits"

    simulate_frame(str(real), str(output), 40.0, np.random.default_rng(3))

    simulated = np.array(fits.getdata(output)).astype(float)
    assert simulated.min() >= 0 and simulated.max() < 400  # no wrap to 65535


def test_a_color_frame_is_smoothed_within_each_bayer_position() -> None:
    """Two colors of very different level stay apart after the smoothing."""
    image = np.zeros((SIZE, SIZE), dtype=np.float32)
    image[0::2, 0::2] = 2000.0  # red-like
    image[1::2, 1::2] = 500.0  # blue-like
    image[0::2, 1::2] = 1000.0
    image[1::2, 0::2] = 1000.0

    mixed = smoothed_sky_frame(image, bayer=False)
    separate = smoothed_sky_frame(image, bayer=True)

    assert np.ptp(mixed[50:-50, 50:-50]) < 200  # colors blurred into one level
    assert separate[100, 100] == pytest.approx(2000.0, abs=1.0)
    assert separate[101, 101] == pytest.approx(500.0, abs=1.0)


def test_a_cube_or_color_image_cannot_be_simulated(tmp_path: Path) -> None:
    """Only a 2-D raw frame is accepted."""
    cube = tmp_path / "cube.fits"
    fits.PrimaryHDU(np.zeros((3, 50, 50), dtype=np.uint16)).writeto(cube)

    with pytest.raises(InvalidArgumentError, match="2-D"):
        simulate_frame(str(cube), str(tmp_path / "out.fits"), 1.0, np.random.default_rng(1))


def _repeat(ratio: float, error: float = 0.02) -> BiasRepeat:
    """Build one finished repeat.

    Parameters
    ----------
    ratio : `float`
        Its ratio.
    error : `float`, optional
        Its uncertainty.

    Returns
    -------
    repeat : `BiasRepeat`
        The repeat.
    """
    return BiasRepeat(ratio * 1e-3, 1e-3, ratio, error, 2e-3, 1.0, 20.0, 100)


def test_the_repeats_combine_to_a_mean_and_a_standard_error() -> None:
    """Repeats give a mean and its standard error."""
    ratio, error = summarize_repeats([_repeat(0.98), _repeat(1.00), _repeat(0.96)])
    assert ratio == pytest.approx(0.98)
    assert error == pytest.approx(0.02 / math.sqrt(3))

    assert summarize_repeats([_repeat(0.97, 0.03)]) == (pytest.approx(0.97), 0.03)
    assert summarize_repeats([]) == (None, None)


def test_the_result_turns_into_plain_data_for_a_json_file() -> None:
    """The repeats become dicts so the file needs no special encoder."""
    measurement = BiasMeasurement(target_id="M 99", camera="Test", ratio=0.98, repeats=[_repeat(0.98)])

    data = measurement.as_dict()

    assert data["ratio"] == pytest.approx(0.98) and data["repeats"][0]["ratio"] == pytest.approx(0.98)
    assert data["target_id"] == "M 99"


def test_the_kept_radius_is_inside_the_apertures_clearance() -> None:
    """No real pixel can lie in an empty aperture."""
    from astrometricslib.pipelines.stacking.post_processing.empty_apertures import FAINT_SOURCE_CLEARANCE_PX

    assert KEEP_RADIUS_PX < FAINT_SOURCE_CLEARANCE_PX


def test_the_whole_flow_stacks_two_halves_and_the_whole_and_leaves_no_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three stacks per repeat; nothing is kept or stored."""
    from astrometricslib.pipelines.stacking import odd_even_bias
    from astrometricslib.pipelines.stacking.test.test_odd_even_difference_noise import (
        FRAME_COUNT,
        _header,
        _Library,
        _Slot,
    )

    library = _Library(tmp_path)
    library.record_library_count(float(FRAME_COUNT))

    def stub_simulate(real_path: str, output_path: str, sigma: float, rng: np.random.Generator) -> None:
        """Write a small stand-in file instead of a simulated frame.

        Parameters
        ----------
        real_path : `str`
            Unused.
        output_path : `str`
            Where to write.
        sigma : `float`
            Unused.
        rng : `numpy.random.Generator`
            Unused.
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header=_header()).writeto(output_path)

    monkeypatch.setattr(odd_even_bias, "simulate_frame", stub_simulate)
    monkeypatch.setattr(
        odd_even_bias, "_noise_sigmas", lambda members, *args: {frame.path: 12.0 for frame in members}
    )
    entries: list[int] = []
    messages: list[str] = []
    work = tmp_path / "work"

    measurement = odd_even_bias.measure_odd_even_bias(
        library.target,
        library.stack_path,
        configuration=library.configuration,
        drivers=library.drivers,
        stacking_slot=lambda: _Slot(entries),
        work_folder=str(work),
        repeats=2,
        progress=messages.append,
    )

    assert len(measurement.repeats) == 2 and len(entries) == 6
    # Every call named the simulated copy of the reference frame.
    assert len(set(library.stacker.requested)) == 2
    assert all(
        path is not None and Path(path).parent.name.startswith("repeat-")
        for path in library.stacker.requested
    )
    # The reference frame is in both halves.
    assert sorted(len(call) for call in library.stacker.calls[:2]) == [12, 13]
    assert len(library.stacker.calls[2]) == FRAME_COUNT
    assert sorted([measurement.odd_half_frames, measurement.even_half_frames]) == [12, 13]
    assert measurement.ratio is not None and 0.2 < measurement.ratio < 1.3
    assert measurement.ratio_error is not None and measurement.ratio_error >= 0
    assert measurement.frame_count == FRAME_COUNT
    assert any("simulating" in text for text in messages)
    # The stacks and simulated frames are gone; no row was stored.
    subsets = Path(library.stack_path).parent / "_subsets"
    assert not subsets.exists() or not any(subsets.iterdir())
    assert not (work / "simulated").exists()
    assert library.rows("stacking.odd_even_difference_noise_stack_unit") == []


def test_a_half_with_too_few_frames_is_refused_before_anything_is_simulated(tmp_path: Path) -> None:
    """The same minimum as the production job applies."""
    from astrometricslib.pipelines.stacking.odd_even_bias import measure_odd_even_bias
    from astrometricslib.pipelines.stacking.test.test_odd_even_difference_noise import _Library, _Slot

    library = _Library(tmp_path, count=12)
    library.record_library_count(12.0)

    with pytest.raises(InvalidArgumentError, match="a half would have"):
        measure_odd_even_bias(
            library.target,
            library.stack_path,
            configuration=library.configuration,
            drivers=library.drivers,
            stacking_slot=lambda: _Slot([]),
            work_folder=str(tmp_path / "work"),
        )
    assert library.stacker.calls == []
