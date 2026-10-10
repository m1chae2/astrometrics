"""Purpose: Unit tests for the raw light-frame check.

Description: Builds small synthetic star fields (Gaussian stars on noisy
sky) so each failure the check looks for can be made on purpose: a trailed
frame, a soft frame, a frame that lost its stars, and a frame the field
moved in. A clean batch must come back unflagged. Further tests cover the
camera-profile saturation level (a clipped 14-bit frame), the faint straight
trail detector (a faint line painted across a star field), the high
star-count flag, and, when the real M 13 sample frames are present, the
five luminance lights those rules were measured on.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import QualityDiagnostics
from astrometricslib.drivers import camera_profile_store
from astrometricslib.foundation.config import AppConfiguration, _TomlSectionedConfig
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.pipelines.shared.quality import raw_frame_check
from astrometricslib.test.golden import measurements as golden_frames
from astrometricslib.test.synthetic.photometry_frame import SyntheticStar, make_photometry_frame

_SIZE = 512
"""Side of each synthetic frame, in pixels."""

_STAR_POSITIONS = np.random.default_rng(7).uniform(30, _SIZE - 30, size=(60, 2))
"""The same 60 star centres in every frame, so shifts can be recovered."""


def _frame(
    sigma_x: float = 1.6,
    sigma_y: float = 1.6,
    shift: tuple[float, float] = (0.0, 0.0),
    star_fraction: float = 1.0,
    trail_length: int = 0,
    seed: int = 0,
) -> np.ndarray:
    """Draw one synthetic frame.

    Parameters
    ----------
    sigma_x, sigma_y : `float`
        Star widths along x and y, in pixels.
    shift : `tuple` [`float`, `float`]
        Move of the whole field, ``(dy, dx)``.
    star_fraction : `float`
        Share of the stars that are drawn.
    trail_length : `int`
        If positive, each star is smeared this many pixels along x.
    seed : `int`
        Noise seed.

    Returns
    -------
    image : `numpy.ndarray`
        The frame as 16-bit counts.
    """
    rng = np.random.default_rng(seed)
    image = rng.normal(1300.0, 15.0, (_SIZE, _SIZE))
    yy, xx = np.mgrid[0:_SIZE, 0:_SIZE]
    count = int(len(_STAR_POSITIONS) * star_fraction)
    for centre_y, centre_x in _STAR_POSITIONS[:count]:
        centre_y, centre_x = centre_y + shift[0], centre_x + shift[1]
        if not (10 < centre_y < _SIZE - 10 and 10 < centre_x < _SIZE - 10):
            continue
        window = (
            slice(int(centre_y) - 40, int(centre_y) + 41),
            slice(int(centre_x) - 40, int(centre_x) + 41),
        )
        y_local, x_local = yy[window], xx[window]
        steps = max(trail_length, 1)
        for step in range(steps):
            x_offset = centre_x + step - steps / 2 if trail_length else centre_x
            profile = np.exp(
                -((x_local - x_offset) ** 2) / (2 * sigma_x**2) - (y_local - centre_y) ** 2 / (2 * sigma_y**2)
            )
            image[window] += 9000.0 / steps * profile
    return np.clip(image, 0, 65535).astype(np.uint16)


def _write_batch(folder: Path, frames: list[np.ndarray]) -> None:
    """Write `frames` as numbered FITS files in `folder`."""
    for index, frame in enumerate(frames):
        fits.PrimaryHDU(frame).writeto(folder / f"Target_Light_Luminance_{index:03d}.fits")


def _clean_batch(count: int = 5) -> list[np.ndarray]:
    """Build `count` clean frames that differ only in their noise.

    Returns
    -------
    frames : `list` [`numpy.ndarray`]
        The frames.
    """
    return [_frame(seed=seed) for seed in range(count)]


def test_a_clean_batch_is_not_flagged(tmp_path: Path) -> None:
    """Frames that differ only in noise raise no flags."""
    _write_batch(tmp_path, _clean_batch())

    report = raw_frame_check.check_raw_frames(folder=str(tmp_path))

    assert report["batch"]["frame_count"] == 5
    assert report["batch"]["flagged_count"] == 0


def test_a_frame_that_lost_its_stars_is_flagged(tmp_path: Path) -> None:
    """A frame with a fifth of the stars reads as trailed or clouded."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(star_fraction=0.2, seed=9)])

    flags = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]["flags"]

    assert any("trailed or clouded" in flag for flag in flags)


def test_a_trailed_frame_is_flagged_for_its_streaks(tmp_path: Path) -> None:
    """Stars smeared 90 pixels long are caught as trails."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(trail_length=90, seed=9)])

    last = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]

    assert last["longest_trail_px"] > 60
    assert any("streak" in flag for flag in last["flags"])


def test_a_soft_frame_is_flagged_for_its_star_width(tmp_path: Path) -> None:
    """Stars 1.6 times as wide as the rest read as soft."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(sigma_x=2.6, sigma_y=2.6, seed=9)])

    flags = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]["flags"]

    assert any("soft" in flag for flag in flags)


def test_an_elongated_frame_is_flagged_for_its_roundness(tmp_path: Path) -> None:
    """Stars twice as wide as tall read as elongated."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(sigma_x=2.4, sigma_y=1.2, seed=9)])

    flags = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]["flags"]

    assert any("elongated" in flag for flag in flags)


def test_the_shift_between_frames_is_measured_and_a_jump_flagged(tmp_path: Path) -> None:
    """A 5 px dither is reported quietly; a 60 px move is flagged."""
    frames = [_frame(seed=0), _frame(shift=(3.0, -4.0), seed=1), _frame(shift=(63.0, -4.0), seed=2)]
    _write_batch(tmp_path, frames)

    report = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"]

    assert report[0]["shift_from_previous_px"] is None
    assert report[1]["shift_from_previous_px"] == pytest.approx([3.0, -4.0], abs=1.0)
    assert report[1]["flags"] == []
    assert report[2]["shift_from_previous_px"] == pytest.approx([60.0, 0.0], abs=1.0)
    assert any("moved" in flag for flag in report[2]["flags"])


def test_last_count_checks_only_the_newest_frames(tmp_path: Path) -> None:
    """The newest two of five frames are the ones measured."""
    _write_batch(tmp_path, _clean_batch())

    report = raw_frame_check.check_raw_frames(folder=str(tmp_path), last_count=2)

    assert [Path(f["path"]).name for f in report["frames"]] == [
        "Target_Light_Luminance_003.fits",
        "Target_Light_Luminance_004.fits",
    ]


def test_an_empty_folder_gives_an_empty_report(tmp_path: Path) -> None:
    """No frames is not an error."""
    assert raw_frame_check.check_raw_frames(folder=str(tmp_path)) == {
        "frames": [],
        "batch": {"frame_count": 0},
    }


def test_the_diagnostics_api_runs_the_check_on_a_folder(tmp_path: Path) -> None:
    """`frame_quality(kind="raw_check")` runs the same check on a folder."""
    _write_batch(tmp_path, _clean_batch(3))

    diagnostics = QualityDiagnostics(AppConfiguration(), MagicMock())
    report = diagnostics.frame_quality(folder_path=str(tmp_path), kind="raw_check", register_job=False)

    assert report.batch["frame_count"] == 3
    assert report.frames_checked == 3


def _use_camera_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, d5300_threshold_adu: float) -> None:
    """Make the camera-profile lookup read a small config written for the test.

    The config holds a generic fallback profile (65000 ADU) and a
    ``Nikon D5300`` profile that clips at 16383 ADU, the 14-bit maximum.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder to write the config file in.
    monkeypatch : `pytest.MonkeyPatch`
        The pytest fixture used to swap in the config.
    d5300_threshold_adu : `float`
        The saturation threshold the D5300 profile lists. The shipped config
        lists 65000, which a 14-bit frame can never reach.
    """
    config_path = tmp_path / "profiles.config.toml"
    config_path.write_text(
        '["Observatory.Camera.Generic"]\n'
        'is_generic_fallback = "true"\n'
        'clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "a test" }\n'
        'saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "a test" }\n'
        "\n"
        '["Observatory.Camera.Nikon D5300"]\n'
        'name_aliases = "Nikon DSLR DSC D5300"\n'
        'clip_ceiling_adu = { value = 16383.0, kind = "measured", source = "a test" }\n'
        f'saturation_threshold_adu = {{ value = {d5300_threshold_adu}, kind = "assumed", source = "x" }}\n'
    )
    profile_config = AppConfiguration.__new__(AppConfiguration)
    profile_config._find_config_file = lambda: config_path
    profile_config.base_dir = tmp_path
    profile_config.config_file_path = None
    profile_config.app_config = _TomlSectionedConfig()
    profile_config.app_config.read(str(config_path))
    monkeypatch.setattr(camera_profile_store, "get_configuration", lambda: profile_config)


def _write_fourteen_bit_frame(path: Path, camera: str | None) -> None:
    """Write a synthetic 14-bit frame with one star clipped at 16383 ADU.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write.
    camera : `str` or `None`
        Value of the ``INSTRUME`` header card, or `None` to leave it out.
    """
    stars = [SyntheticStar(x=100.0, y=100.0, flux_adu=300000.0, fwhm_px=3.5)]
    stars += [SyntheticStar(x=40.0 + 30 * i, y=300.0, flux_adu=4000.0, fwhm_px=3.5) for i in range(10)]
    frame = make_photometry_frame(
        stars, shape=(400, 400), sky_adu=500.0, read_noise_adu=8.0, saturation_adu=16383.0, seed=1
    )
    hdu = fits.PrimaryHDU(np.clip(frame, 0, 16383).astype(np.uint16))
    if camera:
        hdu.header["INSTRUME"] = camera
    hdu.writeto(path)


def test_a_clipped_fourteen_bit_frame_is_saturated_with_its_camera_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A star clipped at 16383 ADU counts as saturated for a D5300 frame.

    The D5300 profile here lists the same threshold (65000) as the shipped
    config, which a 14-bit pixel cannot reach, so the clip ceiling is used and
    the source says so. A frame from an unlisted camera keeps the old 65000
    level and reports no saturation, as every frame did before.
    """
    _use_camera_profiles(tmp_path, monkeypatch, d5300_threshold_adu=65000.0)
    _write_fourteen_bit_frame(tmp_path / "nikon.fits", "Nikon D5300")
    _write_fourteen_bit_frame(tmp_path / "unlisted.fits", "Mystery Camera")

    nikon = raw_frame_check.measure_raw_frame(str(tmp_path / "nikon.fits"))
    unlisted = raw_frame_check.measure_raw_frame(str(tmp_path / "unlisted.fits"))

    assert nikon["saturated_pixels"] > 0
    assert nikon["saturation_threshold_adu"] == pytest.approx(16383.0)
    assert "clip ceiling" in nikon["saturation_threshold_source"]
    assert unlisted["saturated_pixels"] == 0
    assert unlisted["saturation_threshold_adu"] == pytest.approx(65000.0)
    assert "generic fallback" in unlisted["saturation_threshold_source"]


def test_a_reachable_profile_threshold_is_used_as_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A D5300 profile listing 16000 ADU is applied as it stands."""
    _use_camera_profiles(tmp_path, monkeypatch, d5300_threshold_adu=16000.0)
    _write_fourteen_bit_frame(tmp_path / "nikon.fits", "Nikon DSLR DSC D5300")

    measurement = raw_frame_check.measure_raw_frame(str(tmp_path / "nikon.fits"))

    assert measurement["saturation_threshold_adu"] == pytest.approx(16000.0)
    assert "Nikon D5300 profile saturation threshold" in measurement["saturation_threshold_source"]
    assert measurement["saturated_pixels"] > 0


def test_saturation_falls_back_to_65000_when_no_profile_can_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed profile lookup gives 65000 ADU and a source that says why."""

    def fail(camera_name: str | None) -> None:
        """Stand in for a profile lookup that cannot read the config.

        Parameters
        ----------
        camera_name : `str` or `None`
            The camera name, ignored.

        Raises
        ------
        ConfigurationError
            Always.
        """
        raise ConfigurationError("no profiles")

    monkeypatch.setattr(camera_profile_store, "resolve_camera_profile", fail)
    _write_fourteen_bit_frame(tmp_path / "nikon.fits", "Nikon D5300")

    measurement = raw_frame_check.measure_raw_frame(str(tmp_path / "nikon.fits"))

    assert measurement["saturation_threshold_adu"] == pytest.approx(65000.0)
    assert "no camera profile could be read" in measurement["saturation_threshold_source"]
    assert measurement["saturated_pixels"] == 0


_FIELD_SIZE = 1024
"""Side of the larger synthetic frames used for the trail tests, in pixels."""

_LINE_START = (100.0, 40.0)
"""``(x, y)`` where the painted trail starts."""

_LINE_END = (900.0, 760.0)
"""``(x, y)`` where the painted trail ends."""


def _star_field_with_trail(trail_peak_adu: float, seed: int = 0) -> np.ndarray:
    """Make a noisy star field and paint a faint straight trail across it.

    The stars come from `make_photometry_frame`. The trail is a Gaussian
    ridge 4 pixels wide (full width at half maximum) along the line from
    `_LINE_START` to `_LINE_END`. Its peak is a few times the pixel noise but
    well under the 40-noise-unit cut-off of the star detector.

    Parameters
    ----------
    trail_peak_adu : `float`
        Height of the trail above the sky, in ADU. 0 paints no trail.
    seed : `int`, optional
        Noise seed.

    Returns
    -------
    image : `numpy.ndarray`
        The frame as unsigned 16-bit counts.
    """
    rng = np.random.default_rng(3)
    stars = [
        SyntheticStar(
            x=float(rng.uniform(20, _FIELD_SIZE - 20)),
            y=float(rng.uniform(20, _FIELD_SIZE - 20)),
            flux_adu=float(rng.uniform(5000, 60000)),
            fwhm_px=3.5,
        )
        for _ in range(80)
    ]
    image = make_photometry_frame(
        stars, shape=(_FIELD_SIZE, _FIELD_SIZE), sky_adu=300.0, read_noise_adu=20.0, seed=seed
    )
    if trail_peak_adu > 0:
        yy, xx = np.mgrid[0:_FIELD_SIZE, 0:_FIELD_SIZE]
        start, end = np.array(_LINE_START), np.array(_LINE_END)
        direction = (end - start) / np.hypot(*(end - start))
        along = (xx - start[0]) * direction[0] + (yy - start[1]) * direction[1]
        across = (xx - start[0]) * -direction[1] + (yy - start[1]) * direction[0]
        sigma = 4.0 / 2.355
        on_segment = (along >= 0) & (along <= np.hypot(*(end - start)))
        image += trail_peak_adu * np.exp(-(across**2) / (2 * sigma**2)) * on_segment
    return np.clip(image, 0, 65535).astype(np.uint16)


def test_a_faint_trail_under_the_region_cut_off_is_found_and_flagged(tmp_path: Path) -> None:
    """A 4-pixel-wide trail 100 ADU high is reported with its length and angle.

    The trail is below the star detector's cut-off, so the longest-streak
    measurement stays short and the old streak flag stays quiet. The line
    detector still finds it.
    """
    fits.PrimaryHDU(_star_field_with_trail(100.0)).writeto(tmp_path / "Target_Light_Luminance_000.fits")
    expected_length = np.hypot(_LINE_END[0] - _LINE_START[0], _LINE_END[1] - _LINE_START[1])
    expected_angle = np.degrees(np.arctan2(_LINE_END[1] - _LINE_START[1], _LINE_END[0] - _LINE_START[0]))

    measurement = raw_frame_check.measure_raw_frame(str(tmp_path / "Target_Light_Luminance_000.fits"))
    raw_frame_check.flag_frames([measurement])

    assert measurement["longest_trail_px"] < raw_frame_check.TRAIL_FLOOR_PIXELS
    assert measurement["line_trail_px"] == pytest.approx(expected_length, rel=0.1)
    assert measurement["line_trail_angle_deg"] == pytest.approx(expected_angle, abs=1.5)
    assert any("straight trail" in flag and "degrees" in flag for flag in measurement["flags"])
    assert not any("streak" in flag for flag in measurement["flags"])


def test_a_star_field_without_a_trail_has_no_line(tmp_path: Path) -> None:
    """A star field with no trail painted reports no line and no flags."""
    fits.PrimaryHDU(_star_field_with_trail(0.0)).writeto(tmp_path / "clean.fits")

    measurement = raw_frame_check.measure_raw_frame(str(tmp_path / "clean.fits"))
    raw_frame_check.flag_frames([measurement])

    assert measurement["line_trail_px"] == 0
    assert measurement["line_trail_angle_deg"] is None
    assert measurement["flags"] == []


def test_the_shortest_trail_is_set_by_the_diagonal_fraction(tmp_path: Path) -> None:
    """A 0.9 diagonal fraction ignores a trail of 0.55 of the diagonal."""
    fits.PrimaryHDU(_star_field_with_trail(100.0)).writeto(tmp_path / "trail.fits")

    strict = raw_frame_check.measure_raw_frame(str(tmp_path / "trail.fits"), trail_min_diagonal_fraction=0.9)
    default = raw_frame_check.measure_raw_frame(str(tmp_path / "trail.fits"))

    assert strict["line_trail_px"] == 0
    assert default["line_trail_px"] > 0


def _counts_batch(star_counts: list[int]) -> list[dict[str, Any]]:
    """Build bare measurements that differ only in star count.

    Parameters
    ----------
    star_counts : `list` [`int`]
        The star count of each frame, in time order.

    Returns
    -------
    measurements : `list` [`dict`]
        Measurements ready for `flag_frames`, with matching star widths and
        no shift, trail or saturation to flag.
    """
    return [
        {
            "star_count": count,
            "longest_trail_px": 30,
            "line_trail_px": 0,
            "line_trail_angle_deg": None,
            "fwhm_px": 3.5,
            "roundness": 0.95,
            "width_px": 3008,
            "shift_from_previous_px": None if index == 0 else [1.0, 1.0],
        }
        for index, count in enumerate(star_counts)
    ]


def test_a_star_count_far_above_the_batch_median_is_flagged() -> None:
    """900 regions against a median of 620 (1.45 times) is flagged."""
    batch = _counts_batch([600, 610, 620, 630, 900])

    raw_frame_check.flag_frames(batch)

    assert [m["flags"] for m in batch[:4]] == [[], [], [], []]
    assert len(batch[4]["flags"]) == 1
    assert "900 stars against a typical 620" in batch[4]["flags"][0]


def test_a_star_count_just_under_the_high_limit_is_not_flagged() -> None:
    """A count 1.38 times the median stays under the 1.4 limit."""
    batch = _counts_batch([600, 600, 600, 600, 828])

    raw_frame_check.flag_frames(batch)

    assert all(m["flags"] == [] for m in batch)


def test_the_high_count_rule_needs_more_than_one_frame() -> None:
    """One frame has no batch to compare with, so it cannot be too high."""
    batch = _counts_batch([5000])

    raw_frame_check.flag_frames(batch)

    assert batch[0]["flags"] == []


def _m13_paths() -> list[str]:
    """List the five M 13 luminance lights, or skip if they are not real.

    Returns
    -------
    paths : `list` [`str`]
        The frames in time order.
    """
    paths = [golden_frames.frame_path(name) for name in golden_frames.LUMINANCE_FRAMES]
    for path in paths:
        if not path.exists():
            pytest.skip(f"Sample frame missing: {path}")
        if golden_frames.is_lfs_pointer(path):
            pytest.skip(f"{path.name} is a Git LFS pointer, not the frame. Run 'git lfs pull'.")
    return [str(path) for path in paths]


def test_the_real_m13_lights_flag_only_the_frame_with_the_faint_trail() -> None:
    """Only frame 020 is flagged, for its trail and its star count.

    Frames 019, 021, 022 and 023 stay clean. Frame 020 carries a satellite
    or aircraft trail about 3190 px long at 57.2 degrees. It holds 934
    bright regions against a batch median of 624.
    """
    report = raw_frame_check.check_raw_frames(paths=_m13_paths())

    flags = {Path(f["path"]).stem[-3:]: f["flags"] for f in report["frames"]}
    trailed = next(f for f in report["frames"] if f["path"].endswith("020.fits"))
    assert [n for n, frame_flags in flags.items() if frame_flags] == ["020"]
    assert any("straight trail" in flag for flag in flags["020"])
    assert any("934 stars against a typical 624" in flag for flag in flags["020"])
    assert trailed["line_trail_px"] == pytest.approx(3190, rel=0.05)
    assert trailed["line_trail_angle_deg"] == pytest.approx(57.2, abs=1.0)
    assert report["batch"]["flagged_count"] == 1
    assert all(f["line_trail_px"] == 0 for f in report["frames"] if not f["path"].endswith("020.fits"))
