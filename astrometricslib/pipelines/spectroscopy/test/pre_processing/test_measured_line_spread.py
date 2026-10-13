"""Purpose: Tests for the per-spectrum measured line spread and its metrics.

Description: The pipeline measures how wide the blur of one spectrum is,
band by band, from the trail width across the dispersion. These tests check
the measurement on hand-made arrays, then on synthetic spectral frames whose
trail width grows with wavelength by a known amount (the width the extractor
reports must match the injected one within 5 percent in every band), and
check that the chromatic defocus ratio, its flag and the other checkpoint 1
metrics follow. They also check that the pipeline stores the profile on the
result, and that the option to blur templates with the measured profile is off
by default and changes only the profile handed to the analysis when on.
"""

from typing import Any

import numpy as np
import pytest

from astrometricslib.models.measured_line_spread import MeasuredLineSpread
from astrometricslib.models.spectroscopy_quality import StageQualityMetric
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy import pipeline as pipeline_module
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.measured_line_spread import (
    BLUE_WINDOW_ANGSTROM,
    CHROMATIC_DEFOCUS_RATIO_LIMIT,
    HALPHA_ANGSTROM,
    PROFILE_BAND_ANGSTROM,
    PROFILE_END_ANGSTROM,
    PROFILE_START_ANGSTROM,
    RED_WINDOW_ANGSTROM,
    line_spread_checkpoint_items,
    measure_line_spread,
    measured_fwhm_angstrom_at,
    median_fwhm_px,
    to_resolution_profile,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FWHM_PER_SIGMA,
    MINIMUM_RESOLUTION_ELEMENT_PIXELS,
    ResolutionProfile,
)
from astrometricslib.pipelines.spectroscopy.test.test_result_diagnostics import (
    CAMERA_NAME,
    FRAME_SHAPE,
    ZERO_ORDER_XY,
    _ArrayImage,
)
from astrometricslib.test.synthetic import SyntheticSpectralFrame, make_spectral_frame
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

# The generator's straight-line dispersion. The real instrument is within
# 1.5 percent of it over the range used here (grating equation versus line).
GENERATOR_DISPERSION_A_PER_PX = 11.4
TRAIL_TILT_DEGREES = 2.0
# How far a band's measured FWHM may sit from the injected one.
BAND_TOLERANCE = 0.05
# Injected sigma against wavelength: 1.6 px at 4200 A growing in a straight
# line to 3.0 px at 8000 A.
GROWING_SIGMA = ((4200.0, 1.6), (8000.0, 3.0))


def build_line_spread_pipeline(use_measured_line_spread: bool = False) -> SpectroscopyPipeline:
    """Build a pipeline for a 200 lines/mm grating on a synthetic frame.

    The settings give about 11.4 Angstroms per pixel, like the real
    Star Analyzer 200 setup, and follow a trail tilted by two degrees.

    Parameters
    ----------
    use_measured_line_spread : `bool`, optional
        Passed to the pipeline.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name=CAMERA_NAME,
        pixel_size_um=3.76,
        sensor_width_px=FRAME_SHAPE[1],
        sensor_height_px=FRAME_SHAPE[0],
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_lines_per_mm=200.0,
        grating_distance_mm=16.5,
        dispersion_orientation="horizontal",
        dispersion_direction="positive",
        dispersion_start_px=200.0,
        dispersion_angle_degrees=-TRAIL_TILT_DEGREES,
        extraction_method="traced",
        use_flare_mask_extraction=True,
    )
    return SpectroscopyPipeline(config=config, use_measured_line_spread=use_measured_line_spread)


def make_trail_frame(sigma_nodes: tuple[tuple[float, float], ...], seed: int = 0) -> SyntheticSpectralFrame:
    """Make a synthetic spectral frame with a wavelength-dependent trail width.

    Parameters
    ----------
    sigma_nodes : `tuple` [`tuple` [`float`, `float`], ...]
        The ``(wavelength_angstrom, sigma_px)`` pairs of the trail width.
    seed : `int`, optional
        Seed for the frame's noise.

    Returns
    -------
    frame : `SyntheticSpectralFrame`
        The frame, with no absorption lines.
    """
    return make_spectral_frame(
        zero_order_xy=ZERO_ORDER_XY,
        angle_deg=TRAIL_TILT_DEGREES,
        dispersion_a_per_px=GENERATOR_DISPERSION_A_PER_PX,
        trail_length_px=800,
        lines=(),
        shape=FRAME_SHAPE,
        trace_sigma_by_wavelength=sigma_nodes,
        seed=seed,
    )


def extract(
    frame: SyntheticSpectralFrame, pipeline: SpectroscopyPipeline
) -> tuple[dict[str, Any], _ArrayImage]:
    """Extract the star of a synthetic frame with the pipeline.

    Parameters
    ----------
    frame : `SyntheticSpectralFrame`
        The frame to read.
    pipeline : `SpectroscopyPipeline`
        The pipeline whose extractor reads it.

    Returns
    -------
    extraction : `dict`
        The result of the pipeline's single-star extraction.
    image : `_ArrayImage`
        The wrapped frame.
    """
    image = _ArrayImage(frame.image, header={})
    return pipeline._process_single_star(image, ZERO_ORDER_XY, auto_detect_angle=True), image


def injected_fwhm_px_by_band(
    frame: SyntheticSpectralFrame, extraction: dict[str, Any], profile: MeasuredLineSpread
) -> np.ndarray:
    """Work out the injected FWHM, in pixels, of each profile band.

    The truth of a band is the median of the injected width over the samples
    the extractor placed in that band. A sample's injected width is read at
    the column it sits on, so the small difference between the generator's
    straight-line wavelengths and the instrument's grating equation does not
    enter.

    Parameters
    ----------
    frame : `SyntheticSpectralFrame`
        The frame that was extracted.
    extraction : `dict`
        The extraction of that frame.
    profile : `MeasuredLineSpread`
        The profile measured from the extraction.

    Returns
    -------
    truth : `numpy.ndarray`
        The injected FWHM of every band of `profile`, in pixels.
    """
    wavelengths = np.array(extraction["wavelengths"]) * 10.0
    distances = np.array(extraction["sample_distances_px"])
    columns = distances * np.cos(np.radians(TRAIL_TILT_DEGREES))
    sigma = np.asarray(frame.trace_sigma_at(columns * GENERATOR_DISPERSION_A_PER_PX))
    truth = []
    for centre in profile.wavelength_angstrom:
        low = PROFILE_START_ANGSTROM + PROFILE_BAND_ANGSTROM * np.floor(
            (centre - PROFILE_START_ANGSTROM) / PROFILE_BAND_ANGSTROM
        )
        high = min(low + PROFILE_BAND_ANGSTROM, PROFILE_END_ANGSTROM)
        in_band = (wavelengths >= low) & (wavelengths < high)
        truth.append(FWHM_PER_SIGMA * float(np.median(sigma[in_band])))
    return np.array(truth)


def metrics_by_name(
    wavelengths: np.ndarray, widths: np.ndarray, stored: ResolutionProfile | None = None
) -> tuple[dict[str, StageQualityMetric], list[str]]:
    """Build the checkpoint items for arrays and index the metrics by name.

    Parameters
    ----------
    wavelengths : `numpy.ndarray`
        Wavelengths, in Angstroms.
    widths : `numpy.ndarray`
        The trail sigma at each wavelength, in pixels.
    stored : `ResolutionProfile`, optional
        The stored profile to compare with.

    Returns
    -------
    metrics : `dict`
        The metrics keyed by name.
    flags : `list` [`str`]
        The flags.
    """
    profile = measure_line_spread(wavelengths, widths)
    metrics, flags = line_spread_checkpoint_items(wavelengths, widths, profile, stored)
    return {entry.name: entry for entry in metrics}, flags


def straight_trail(
    sigma_blue: float, sigma_red: float, dispersion: float = 11.4
) -> tuple[np.ndarray, np.ndarray]:
    """Make wavelengths and trail widths that change in a straight line.

    Parameters
    ----------
    sigma_blue : `float`
        The trail sigma at 4200 A, in pixels.
    sigma_red : `float`
        The trail sigma at 8000 A, in pixels.
    dispersion : `float`, optional
        Angstroms per pixel.

    Returns
    -------
    wavelengths, widths : `numpy.ndarray`
        Wavelengths from 3800 to 9000 A, one per pixel, and the sigma at each.
    """
    wavelengths = np.arange(3800.0, 9000.0, dispersion)
    widths = np.interp(wavelengths, [4200.0, 8000.0], [sigma_blue, sigma_red])
    return wavelengths, widths


def test_a_constant_width_gives_a_flat_profile_with_no_scatter() -> None:
    """Equal widths give equal FWHM in every band and zero scatter."""
    wavelengths, widths = straight_trail(2.0, 2.0)

    profile = measure_line_spread(wavelengths, widths)

    assert profile is not None
    assert profile.wavelength_angstrom[0] == pytest.approx(4400.0)
    assert profile.wavelength_angstrom[-1] == pytest.approx(7900.0)
    assert profile.fwhm_px == pytest.approx([2.0 * FWHM_PER_SIGMA] * len(profile.fwhm_px))
    assert profile.scatter_px == pytest.approx([0.0] * len(profile.fwhm_px), abs=1e-12)
    # 11.4 Angstroms per pixel: the FWHM in Angstroms is the pixels times that.
    assert profile.fwhm_angstrom == pytest.approx([2.0 * FWHM_PER_SIGMA * 11.4] * len(profile.fwhm_px))
    assert all(count >= 10 for count in profile.sample_count)


def test_the_conversion_to_angstrom_uses_the_local_dispersion() -> None:
    """A pixel covers more Angstroms where the dispersion is larger."""
    wavelengths = np.concatenate([np.arange(3800.0, 6000.0, 10.0), np.arange(6000.0, 9000.0, 12.0)])
    widths = np.full(wavelengths.shape, 2.0)

    profile = measure_line_spread(wavelengths, widths)

    assert profile is not None
    blue = measured_fwhm_angstrom_at(profile, 4800.0)
    red = measured_fwhm_angstrom_at(profile, 7600.0)
    assert blue == pytest.approx(2.0 * FWHM_PER_SIGMA * 10.0, rel=1e-6)
    assert red == pytest.approx(2.0 * FWHM_PER_SIGMA * 12.0, rel=1e-6)


def test_sample_distances_set_the_dispersion_when_steps_are_not_one_pixel() -> None:
    """With distances given, the dispersion is Angstroms per pixel."""
    distances = np.arange(0.0, 700.0, 0.5)
    wavelengths = 3800.0 + 11.4 * distances
    widths = np.full(distances.shape, 2.0)

    profile = measure_line_spread(wavelengths, widths, distances)

    assert profile is not None
    assert profile.fwhm_angstrom[0] == pytest.approx(2.0 * FWHM_PER_SIGMA * 11.4, rel=1e-6)


def test_failed_fits_are_ignored_and_a_few_bad_fits_do_not_move_the_median() -> None:
    """Failed fits (0.0) and wild outliers do not set the band."""
    wavelengths, widths = straight_trail(2.0, 2.0)
    widths[::5] = 0.0
    widths[1::17] = 40.0

    profile = measure_line_spread(wavelengths, widths)

    assert profile is not None
    assert profile.fwhm_px == pytest.approx([2.0 * FWHM_PER_SIGMA] * len(profile.fwhm_px))


@pytest.mark.parametrize("missing", [None, [], [1.0, 2.0]])
def test_missing_or_mismatched_widths_give_no_profile(missing: object) -> None:
    """No trail width, or one that does not line up, gives `None`."""
    wavelengths, _ = straight_trail(2.0, 2.0)

    assert measure_line_spread(wavelengths, missing) is None
    assert measure_line_spread(None, missing) is None


def test_a_spectrum_with_almost_no_fits_gives_no_profile() -> None:
    """Fewer than the minimum fits per band leaves fewer than two bands."""
    wavelengths, widths = straight_trail(2.0, 2.0)
    widths[:] = 0.0
    widths[100:105] = 2.0

    assert measure_line_spread(wavelengths, widths) is None


def test_the_profile_is_never_extrapolated() -> None:
    """Reading outside the first and last band centre gives `None`."""
    wavelengths, widths = straight_trail(2.0, 2.0)
    profile = measure_line_spread(wavelengths, widths)

    assert measured_fwhm_angstrom_at(profile, 4000.0) is None
    assert measured_fwhm_angstrom_at(profile, 8100.0) is None
    assert measured_fwhm_angstrom_at(None, 6000.0) is None


def test_the_resolution_profile_is_raised_to_the_sampling_limit() -> None:
    """A band sharper than two pixels is raised to two pixels."""
    profile = MeasuredLineSpread(
        wavelength_angstrom=[4400.0, 4800.0],
        fwhm_angstrom=[10.0 * 1.0, 11.4 * 3.0],
        fwhm_px=[1.0, 3.0],
        scatter_px=[0.0, 0.0],
        sample_count=[30, 30],
    )

    resolution = to_resolution_profile(profile)

    assert resolution is not None
    assert resolution.resolution_element_angstrom[0] == pytest.approx(
        10.0 * MINIMUM_RESOLUTION_ELEMENT_PIXELS
    )
    assert resolution.resolution_element_angstrom[1] == pytest.approx(11.4 * 3.0)
    assert to_resolution_profile(None) is None


@pytest.mark.parametrize("seed", [0, 1])
def test_the_extractor_recovers_a_growing_trail_width_in_every_band(seed: int) -> None:
    """Sigma growing from 1.6 to 3.0 px is recovered within 5 percent."""
    frame = make_trail_frame(GROWING_SIGMA, seed=seed)
    extraction, _ = extract(frame, build_line_spread_pipeline())

    profile = measure_line_spread(
        [w * 10.0 for w in extraction["wavelengths"]],
        extraction["trail_width_px"],
        extraction["sample_distances_px"],
    )

    assert profile is not None
    assert len(profile.wavelength_angstrom) == 10
    truth = injected_fwhm_px_by_band(frame, extraction, profile)
    assert np.array(profile.fwhm_px) == pytest.approx(truth, rel=BAND_TOLERANCE)
    # The band widths increase from blue to red, like the injected width.
    assert np.all(np.diff(profile.fwhm_px) > 0)
    # The dispersion is near 11 Angstroms per pixel, so the FWHM in Angstroms
    # follows.
    ratio = np.array(profile.fwhm_angstrom) / np.array(profile.fwhm_px)
    assert np.all((ratio > 10.5) & (ratio < 11.6))


def test_the_defocus_ratio_follows_the_injected_widths_and_flags_a_defocused_red() -> None:
    """A red end 40 percent wider than the blue is flagged."""
    frame = make_trail_frame(GROWING_SIGMA)
    pipeline = build_line_spread_pipeline()
    extraction, image = extract(frame, pipeline)
    star = StellarObject(id="Defocused")

    pipeline._apply_result_to_stellar_object(star, extraction, image)

    assert star.spectroscopy is not None
    checkpoint = star.spectroscopy.stage_quality[1]
    assert checkpoint.stage == "pre_processing"
    metrics = {entry.name: entry for entry in checkpoint.metrics}
    wavelengths = np.array(extraction["wavelengths"]) * 10.0
    sigma = np.asarray(
        frame.trace_sigma_at(
            np.array(extraction["sample_distances_px"])
            * np.cos(np.radians(TRAIL_TILT_DEGREES))
            * GENERATOR_DISPERSION_A_PER_PX
        )
    )
    blue = (wavelengths >= BLUE_WINDOW_ANGSTROM[0]) & (wavelengths < BLUE_WINDOW_ANGSTROM[1])
    red = (wavelengths >= RED_WINDOW_ANGSTROM[0]) & (wavelengths < RED_WINDOW_ANGSTROM[1])
    true_blue = FWHM_PER_SIGMA * float(np.median(sigma[blue]))
    true_red = FWHM_PER_SIGMA * float(np.median(sigma[red]))

    assert metrics["trail_fwhm_blue_px"].value == pytest.approx(true_blue, rel=BAND_TOLERANCE)
    assert metrics["trail_fwhm_red_px"].value == pytest.approx(true_red, rel=BAND_TOLERANCE)
    ratio = metrics["chromatic_defocus_ratio"]
    assert ratio.value == pytest.approx(true_red / true_blue, rel=BAND_TOLERANCE)
    assert ratio.value > CHROMATIC_DEFOCUS_RATIO_LIMIT
    assert ratio.limit == CHROMATIC_DEFOCUS_RATIO_LIMIT
    assert ratio.passed is False
    assert "designed" in ratio.note
    assert "chromatic_defocus" in checkpoint.flags
    # The older input-quality numbers are all still there.
    assert {"resolution_element", "signal_to_noise", "valid_fraction"} <= set(metrics)


@pytest.mark.parametrize(
    ("nodes", "should_flag"),
    [
        (((4200.0, 2.0), (8000.0, 2.0)), False),
        (((4200.0, 2.0), (8000.0, 1.85)), False),
        (((4200.0, 1.8), (8000.0, 2.4)), False),
        (((4200.0, 1.6), (8000.0, 3.0)), True),
    ],
)
def test_the_flag_follows_the_ratio_on_extracted_frames(
    nodes: tuple[tuple[float, float], ...], should_flag: bool
) -> None:
    """A flat or mild change is not flagged. A strong growth is."""
    frame = make_trail_frame(nodes)
    pipeline = build_line_spread_pipeline()
    extraction, image = extract(frame, pipeline)
    star = StellarObject(id="Frame")

    pipeline._apply_result_to_stellar_object(star, extraction, image)

    assert star.spectroscopy is not None
    checkpoint = star.spectroscopy.stage_quality[1]
    ratio = next(entry for entry in checkpoint.metrics if entry.name == "chromatic_defocus_ratio")
    assert ("chromatic_defocus" in checkpoint.flags) is should_flag
    assert ratio.passed is (not should_flag)


def test_an_in_focus_ratio_from_seeing_alone_is_below_one_and_passes() -> None:
    """Widths that follow the seeing law (wavelength^-0.2) give about 0.92."""
    wavelengths = np.arange(3800.0, 9000.0, 11.4)
    widths = 2.0 * (wavelengths / 5500.0) ** -0.2

    metrics, flags = metrics_by_name(wavelengths, widths)

    assert metrics["chromatic_defocus_ratio"].value == pytest.approx(0.93, abs=0.015)
    assert metrics["chromatic_defocus_ratio"].passed is True
    assert flags == []


def test_the_limit_is_exceeded_only_above_it() -> None:
    """A ratio just under the limit passes and one just over it fails."""
    wavelengths = np.arange(3800.0, 9000.0, 11.4)
    under = np.where(wavelengths < 5600.0, 2.0, 2.0 * 1.28)
    over = np.where(wavelengths < 5600.0, 2.0, 2.0 * 1.33)

    under_metrics, under_flags = metrics_by_name(wavelengths, under)
    over_metrics, over_flags = metrics_by_name(wavelengths, over)

    assert under_metrics["chromatic_defocus_ratio"].passed is True
    assert under_flags == []
    assert over_metrics["chromatic_defocus_ratio"].passed is False
    assert over_flags == ["chromatic_defocus"]


def test_the_halpha_ratio_compares_the_measured_and_stored_profiles() -> None:
    """The ratio is the measured FWHM at 6563 A over the stored value."""
    wavelengths = np.arange(3800.0, 9000.0, 11.4)
    widths = np.full(wavelengths.shape, 2.0)
    stored = ResolutionProfile(np.array([4200.0, 9000.0]), np.array([148.0, 148.0]))

    metrics, _ = metrics_by_name(wavelengths, widths, stored)

    measured = 2.0 * FWHM_PER_SIGMA * 11.4
    entry = metrics["measured_vs_stored_line_spread_ratio_halpha"]
    assert entry.value == pytest.approx(measured / 148.0)
    assert entry.limit is None
    assert entry.passed is None
    assert HALPHA_ANGSTROM == pytest.approx(6563.0)


def test_without_a_trail_width_every_new_metric_is_empty_and_nothing_is_flagged() -> None:
    """Without trail widths the metrics exist but have no value."""
    wavelengths = np.arange(3800.0, 9000.0, 11.4)

    metrics, flags = line_spread_checkpoint_items(wavelengths, None, None, None)

    by_name = {entry.name: entry for entry in metrics}
    assert set(by_name) == {
        "trail_fwhm_blue_px",
        "trail_fwhm_red_px",
        "chromatic_defocus_ratio",
        "measured_vs_stored_line_spread_ratio_halpha",
    }
    assert all(entry.value is None and entry.passed is None for entry in by_name.values())
    assert flags == []
    assert median_fwhm_px(wavelengths, None, BLUE_WINDOW_ANGSTROM) is None


def test_the_pipeline_stores_the_measured_profile_on_the_result() -> None:
    """The result holds the profile in plain floats; it survives JSON."""
    frame = make_trail_frame(GROWING_SIGMA)
    pipeline = build_line_spread_pipeline()
    extraction, image = extract(frame, pipeline)
    star = StellarObject(id="Stored")

    pipeline._apply_result_to_stellar_object(star, extraction, image)

    result = star.spectroscopy
    assert result is not None
    assert result.measured_line_spread is not None
    assert all(type(value) is float for value in result.measured_line_spread.fwhm_px)
    restored = MeasuredLineSpread.model_validate_json(
        result.measured_line_spread.model_dump_json(by_alias=True)
    )
    assert restored == result.measured_line_spread
    assert "measuredLineSpread" in result.model_dump(by_alias=True)


def test_the_option_to_use_the_measured_profile_is_off_by_default() -> None:
    """A default pipeline blurs with the stored profile."""
    assert build_line_spread_pipeline().use_measured_line_spread is False
    assert SpectroscopyPipeline(config=build_line_spread_pipeline().config).use_measured_line_spread is False


@pytest.mark.parametrize("use_measured", [False, True])
def test_the_option_changes_only_the_profile_handed_to_the_analysis(
    use_measured: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off: the stored profile. On: this spectrum's own measured profile."""
    frame = make_trail_frame(GROWING_SIGMA)
    pipeline = build_line_spread_pipeline(use_measured)
    extraction, image = extract(frame, pipeline)
    seen: list[ResolutionProfile | None] = []
    real_analyze = pipeline_module.analyze_spectrum

    def spy(*args: object, **kwargs: Any) -> object:
        """Record the resolution profile, then run the real analysis.

        Returns
        -------
        analysis : `object`
            What the real analysis returns.
        """
        seen.append(kwargs.get("resolution_profile"))
        return real_analyze(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "analyze_spectrum", spy)
    star = StellarObject(id="Option")

    pipeline._apply_result_to_stellar_object(star, extraction, image)

    assert star.spectroscopy is not None
    assert len(seen) == 1
    profile = seen[0]
    assert profile is not None
    if use_measured:
        measured = star.spectroscopy.measured_line_spread
        assert measured is not None
        assert profile.wavelength_angstrom.tolist() == measured.wavelength_angstrom
        assert profile.resolution_element_angstrom.tolist() == pytest.approx(measured.fwhm_angstrom)
    else:
        assert profile is pipeline.line_spread_profile
