"""Tests for the noise of the difference of two half stacks.

A fake stacking program writes a synthetic stack for each half: the same fixed
structure (faint stars and a smooth texture, standing for what every frame
shares) plus fresh white noise. The tests check that the difference recovers
the injected random noise where the plain empty-aperture noise cannot, that
the split is disjoint, alternates in time and shares only the reference frame,
that the weights formula gives half the difference for equal halves, that the
halves are deleted and the library stack is left alone, and that every
refusal stores not-measured rows with a reason and no made-up number. They
also check that the registration reference is handed to the stacker for both
halves, that a conflicting library stack stops the job, that the halves are
deleted after a stacker failure, that a spectral stack is refused, that the
zero (uncovered) pixels of either stack are kept out of the apertures, and, on
synthetic frames with unequal noise and weights, that the implied noise of the
library stack follows the formulas.
"""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import gaussian_filter

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.stacking_driver import StackingDriver, StackRunResult, StackSettings
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError
from astrometricslib.models.quality.measurement import MeasurementStatus, SubjectLevel
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking import subset_noise_run
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
    STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
    STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO,
    STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED,
    STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT,
    STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
    STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
    STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
    STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
    STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import FrameNoiseInput
from astrometricslib.pipelines.stacking.post_processing.stack_membership import effective_frame_count
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    APERTURE_FILE_NAME,
    measure_difference_noise,
    measure_stack_noise,
    save_noise_sums,
    signed_aperture_sum,
)
from astrometricslib.pipelines.stacking.subset_noise_run import (
    HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF,
    difference_variance_factor,
    full_stack_noise_equivalent,
    full_stack_noise_equivalent_from_model,
    model_variance_ratio,
    run_half_difference_noise,
    split_into_halves,
)
from astrometricslib.pipelines.stacking.target_stack_guard import library_stack_guard

NINE_METRICS = (
    STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT,
    STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED,
    STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
    STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
    STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
    STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR,
    STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO,
    STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
    STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
)

SIZE = 900
SKY_LEVEL = 100.0
STAR_POSITIONS = [(150 + 90 * column, 200 + 110 * row) for column in range(8) for row in range(6)]
STACK_NAME = "M99_L_Stacked.fits"
RUN_TIME = datetime(2026, 10, 23, tzinfo=UTC)
FRAME_COUNT = 24
INJECTED_FRAME_NOISE = 12.0
TEXTURE_AMPLITUDE = 2.0


def _header() -> fits.Header:
    """Build a header with a plain tangent-plane sky solution.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A header with a 2 arcsecond pixel scale.
    """
    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = SIZE / 2
    header["CRPIX2"] = SIZE / 2
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 2.0
    header["CDELT1"] = -2.0 / 3600.0
    header["CDELT2"] = 2.0 / 3600.0
    return header


def _shared_structure(*, texture: bool = True) -> np.ndarray:
    """Build what every frame shares: sky, round stars and smooth texture.

    The texture is white noise smoothed over about 3 px, so it is the size of
    a sky aperture and does not average away, like a faint nebula.

    Parameters
    ----------
    texture : `bool`, optional
        Whether to add the smooth texture. Without it only the sky and the
        stars remain.

    Returns
    -------
    image : `numpy.ndarray`
        The structure, the same on every call.
    """
    generator = np.random.default_rng(2026)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = np.full((SIZE, SIZE), SKY_LEVEL)
    amplitudes = np.linspace(0.3, 1.0, len(STAR_POSITIONS)) * 2000.0
    for (x, y), amplitude in zip(STAR_POSITIONS, amplitudes, strict=True):
        image += amplitude * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * 2.0**2))
    if not texture:
        return image
    smooth = gaussian_filter(generator.normal(0.0, 1.0, (SIZE, SIZE)), 3.0)
    return image + TEXTURE_AMPLITUDE * smooth / smooth.std()


def _write(path: str, noise_sigma: float, seed: int, *, texture: bool = True) -> None:
    """Write a synthetic stack: shared structure plus white noise.

    Parameters
    ----------
    path : `str`
        Where to write.
    noise_sigma : `float`
        The per-pixel noise.
    seed : `int`
        Seed of the noise.
    texture : `bool`, optional
        Whether to include the smooth shared texture. Without it only the
        sky, the stars and the noise are written.
    """
    generator = np.random.default_rng(seed)
    image = _shared_structure(texture=texture)
    image = image + generator.normal(0.0, noise_sigma, (SIZE, SIZE))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(image.astype(np.float32), header=_header()).writeto(path, overwrite=True)


class FakeHalfStacker(StackingDriver):
    """A stacking program whose stacks hold shared structure and fresh noise.

    Attributes
    ----------
    calls : `list` [`list` [`str`]]
        The frame paths of each `stack_batch` call.
    requested : `list` [`str` or `None`]
        The reference frame each call was asked to use.
    reference : `str` or `None`
        A frame it always reports as the registration reference. `None`
        makes it report the requested frame when `obey_request` is set and
        it is among the frames, and the first frame otherwise.
    obey_request : `bool`
        Whether a requested reference frame is used. A stacker that does
        not obey picks the first frame of each call (so two halves disagree).
    fail : `bool`
        Whether every `stack_batch` call raises.
    fail_on_call : `int` or `None`
        The 1-based call that raises (the calls before it write a stack).
    """

    name = "FakeHalf"

    def __init__(
        self,
        reference: str | None = None,
        *,
        fail: bool = False,
        obey_request: bool = True,
        fail_on_call: int | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.requested: list[str | None] = []
        self.reference = reference
        self.fail = fail
        self.obey_request = obey_request
        self.fail_on_call = fail_on_call
        self.attempts = 0

    def version(self) -> str:
        """Report a made-up version.

        Returns
        -------
        version : `str`
            ``"0"``.
        """
        return "0"

    def read_stack_artifacts(self, stacked_path: str) -> dict[str, Any]:
        """Report that no side files exist.

        Parameters
        ----------
        stacked_path : `str`
            The stack.

        Returns
        -------
        artifacts : `dict`
            Nothing.
        """
        return {}

    def stack_batch(
        self,
        frames: list[Any],
        target_id: str,
        output_file: str,
        log_file: str | None,
        is_spectral: bool,
        settings: StackSettings,
        registration: str | None = None,
        job_id: str | None = None,
        steps: Any = None,
    ) -> StackRunResult:
        """Write a stack of the shared structure and fresh noise.

        Parameters
        ----------
        frames : `list`
            The frames, as dicts.
        target_id : `str`
            The target.
        output_file : `str`
            Where to write the stack.
        log_file : `str` or `None`
            Unused.
        is_spectral : `bool`
            Unused.
        settings : `StackSettings`
            Its ``reference_frame_path`` is the reference asked for.
        registration : `str` or `None`, optional
            Unused.
        job_id : `str` or `None`, optional
            Unused.
        steps : `PipelineSteps` or `None`, optional
            Unused.

        Returns
        -------
        result : `StackRunResult`
            The output path and diagnostics that name the reference frame.

        Raises
        ------
        RuntimeError
            If the stacker was made to fail.
        """
        self.attempts += 1
        if self.fail or self.fail_on_call == self.attempts:
            raise RuntimeError("Siril stopped")
        self.calls.append([frame["path"] for frame in frames])
        self.requested.append(settings.reference_frame_path)
        paths = [frame["path"] for frame in frames]
        _write(output_file, INJECTED_FRAME_NOISE / math.sqrt(len(frames)), seed=100 + len(self.calls))
        if self.reference is not None:
            reference = self.reference
        elif self.obey_request and settings.reference_frame_path in paths:
            reference = settings.reference_frame_path
        else:
            reference = paths[0]
        diagnostics = {
            "symlinked_light_paths": paths,
            "registration_frames": [
                {"dx": 0.0 if path == reference else 1.5, "dy": 0.0 if path == reference else -0.5}
                | {"star_count": 100}
                for path in paths
            ],
        }
        return StackRunResult(stacked_path=output_file, diagnostics=diagnostics, engine_name=self.name)


class _Slot:
    """A stacking slot that counts how often it is entered."""

    def __init__(self, entries: list[int]) -> None:
        self._entries = entries

    def __enter__(self) -> None:
        """Count one entry."""
        self._entries.append(1)

    def __exit__(self, *_exception: object) -> None:
        """Leave without hiding an exception."""


def _frames(count: int = FRAME_COUNT, root: str = "/frames", **fields: Any) -> list[FrameRecord]:
    """Build frames one night apart in minutes.

    Parameters
    ----------
    count : `int`, optional
        How many frames.
    root : `str`, optional
        The folder the frame paths are under.
    **fields
        More fields for every frame record (a spectral filter, say).

    Returns
    -------
    frames : `list` [`FrameRecord`]
        Frames of 60 s from one camera, 2 minutes apart.
    """
    start = datetime(2026, 10, 1, 22, 0).timestamp()
    return [
        FrameRecord(
            path=f"{root}/lights/M 99/Scope/Cam/f{index:03d}.fits",
            camera="Cam",
            exposure="60",
            timestamp=start + index * 120.0,
            **fields,
        )
        for index in range(count)
    ]


class _Library:
    """A target with a library stack on disk, its record and its apertures.

    Attributes
    ----------
    target : `Target`
        The target, with the library stack's registration reference recorded.
    stack_path : `str`
        The library stack.
    configuration : `SimpleNamespace`
        The settings.
    stacker : `FakeHalfStacker`
        The fake stacking program.
    drivers : `Drivers`
        Holds the stacker.
    slot_entries : `list` [`int`]
        One entry for each time the stacking slot was entered.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        count: int = FRAME_COUNT,
        reference_index: int | None = 5,
        sums: bool = True,
        apertures: bool = True,
        stacker: FakeHalfStacker | None = None,
        frame_fields: dict[str, Any] | None = None,
    ) -> None:
        self.configuration = SimpleNamespace(
            get_library_path=lambda: str(tmp_path / "library"),
            get_stack_weight=lambda: None,
            get_trim_noisy_stack_edges_enabled=lambda: False,
        )
        frames = _frames(count, str(tmp_path / "frames"), **(frame_fields or {}))
        self.target = Target(id="M 99", frames=frames)
        folder = tmp_path / "stacks" / "lights" / "M 99"
        self.stack_path = str(folder / STACK_NAME)
        _write(self.stack_path, INJECTED_FRAME_NOISE / math.sqrt(count), seed=99)
        if apertures:
            outcome = measure_stack_noise(self.stack_path, None, str(folder / APERTURE_FILE_NAME))
            assert outcome.result is not None
            if sums:
                save_noise_sums(self.stack_path, outcome.result)
        body = {"fingerprint": "x", "record": {"frames": [[frame.path, 1, 1] for frame in frames]}}
        (folder / "M99_L_Stacked_inputs.json").write_text(json.dumps(body), encoding="utf-8")
        if reference_index is not None:
            # Only the reference frame is read from disk, for its header.
            Path(frames[reference_index].path).parent.mkdir(parents=True, exist_ok=True)
            fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header=_header()).writeto(
                frames[reference_index].path
            )
            metrics = SimpleNamespace(registration_reference_frame=frames[reference_index].path)
            object.__setattr__(
                self.target.stacking, "quality_summary", SimpleNamespace(stacking_metrics=metrics)
            )
        self.reference = None if reference_index is None else frames[reference_index].path
        self.stacker = stacker or FakeHalfStacker()
        self.drivers = Drivers(stacking=self.stacker)
        self.slot_entries: list[int] = []

    def context(self) -> PipelineRunContext:
        """Build the run context.

        Returns
        -------
        context : `PipelineRunContext`
            The context for the target.
        """
        return PipelineRunContext("M 99", "stacking", "1.3.0", "run-1", "run-1", RUN_TIME)

    def rows(self, metric: str) -> list[Any]:
        """Read the stored rows of one metric.

        Parameters
        ----------
        metric : `str`
            The metric.

        Returns
        -------
        rows : `list`
            The stored rows.
        """
        return QualityStore.from_configuration(self.configuration).query(metric=metric, target_id="M 99")

    def record_library_count(self, effective_count: float) -> None:
        """Store the library stack's effective count and equal frame weights.

        Parameters
        ----------
        effective_count : `float`
            The library stack's effective frame count.
        """
        from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
        from astrometricslib.pipelines.shared.quality.pipeline_measurements import build_measurement
        from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
        from astrometricslib.pipelines.stacking.metrics import (
            STACKING_EFFECTIVE_FRAME_COUNT,
            STACKING_FRAME_WEIGHT,
            STACKING_METRICS,
        )

        specs = {spec.name: spec for spec in STACKING_METRICS}
        context = self.context()
        stack_subject = context.stack_subject(STACK_NAME)
        rows = [
            build_measurement(specs[STACKING_EFFECTIVE_FRAME_COUNT], effective_count, context, stack_subject)
        ]
        for frame in self.target.frames:
            subject = stack_subject.model_copy(
                update={
                    "level": SubjectLevel.FRAME,
                    "subject_id": frame_subject_id(frame.path, "M 99"),
                }
            )
            rows.append(build_measurement(specs[STACKING_FRAME_WEIGHT], 1.0, context, subject))
        record_measurements(QualityStore.from_configuration(self.configuration), rows)

    def run(self, **keywords: Any) -> Any:
        """Run the half-difference job.

        Parameters
        ----------
        **keywords
            Replacements for the call's keyword arguments.

        Returns
        -------
        result : `HalfDifferenceNoiseResult`
            The job's result.
        """
        arguments = {
            "context": self.context(),
            "configuration": self.configuration,
            "drivers": self.drivers,
            "stacking_slot": lambda: _Slot(self.slot_entries),
        }
        arguments.update(keywords)
        return run_half_difference_noise(self.target, self.stack_path, **arguments)


def test_the_split_is_disjoint_alternates_in_time_and_shares_only_the_reference() -> None:
    """Odd and even places split the frames; the reference frame is in both."""
    frames = _frames(11)
    split = split_into_halves(list(reversed(frames)), frames[4].path)
    odd = [frame.path for frame in split.odd]
    even = [frame.path for frame in split.even]
    assert set(odd) & set(even) == {frames[4].path}
    assert set(odd) | set(even) == {frame.path for frame in frames}
    assert odd == sorted(odd)
    assert even == sorted(even)
    # The 1st, 3rd, ... go to the odd half; frame 4 is already odd, so it is
    # added to the even half.
    assert [path for path in odd if path != frames[4].path] == [frame.path for frame in frames[0::2][:2]] + [
        frame.path for frame in frames[0::2][3:]
    ]
    assert [path for path in even if path != frames[4].path] == [frame.path for frame in frames[1::2]]
    assert len(split.odd) == 6
    assert len(split.even) == 6


def test_the_split_leaves_out_undated_frames_and_refuses_an_unknown_reference() -> None:
    """An undated frame is in neither half; the reference must be dated."""
    frames = _frames(8)
    undated = frames[3].model_copy(update={"timestamp": None})
    split = split_into_halves([*frames[:3], undated, *frames[4:]], frames[0].path)
    assert split.left_out == 1
    assert undated.path not in {frame.path for frame in (*split.odd, *split.even)}
    with pytest.raises(InvalidArgumentError):
        split_into_halves(frames, "/not/a/member.fits")
    with pytest.raises(InvalidArgumentError):
        split_into_halves([*frames[:3], undated, *frames[4:]], undated.path)


def test_equal_disjoint_halves_give_half_the_difference() -> None:
    """For N equal frames per half, the stack's noise is half of it."""
    odd = {f"o{index}": 1 / 10 for index in range(10)}
    even = {f"e{index}": 1 / 10 for index in range(10)}
    assert full_stack_noise_equivalent(8.0, 20.0, odd, even) == pytest.approx(4.0)


def test_a_shared_frame_and_unequal_weights_change_the_equivalent_by_the_formula() -> None:
    """The equivalent is difference / sqrt(N_eff,library x sum((a - b)^2))."""
    odd = {"s": 0.5, "a": 0.5}
    even = {"s": 0.25, "b": 0.75}
    factor = (0.5 - 0.25) ** 2 + 0.5**2 + 0.75**2
    assert full_stack_noise_equivalent(3.0, 4.0, odd, even) == pytest.approx(3.0 / math.sqrt(4.0 * factor))


def test_the_variance_factor_of_disjoint_halves_is_the_sum_of_their_inverse_counts() -> None:
    """For disjoint halves F is 1/N_odd + 1/N_even; sharing lowers it."""
    odd = {"a": 0.5, "b": 0.5}
    even = {"c": 0.25, "d": 0.75}
    assert difference_variance_factor(odd, even) == pytest.approx(1 / 2 + (0.25**2 + 0.75**2))
    shared_odd = {"s": 0.5, "a": 0.5}
    shared_even = {"s": 0.25, "d": 0.75}
    disjoint = 0.5**2 + 0.5**2 + 0.25**2 + 0.75**2
    assert difference_variance_factor(shared_odd, shared_even) == pytest.approx(disjoint - 2 * 0.5 * 0.25)
    assert difference_variance_factor(None, even) is None
    assert difference_variance_factor(odd, None) is None


def test_the_equivalent_is_none_when_a_weight_or_the_count_is_unknown() -> None:
    """No value is made up from missing inputs."""
    shares = {"a": 1.0}
    assert full_stack_noise_equivalent(1.0, None, shares, shares) is None
    assert full_stack_noise_equivalent(1.0, 10.0, None, shares) is None
    assert full_stack_noise_equivalent(1.0, 10.0, shares, None) is None
    assert full_stack_noise_equivalent(1.0, 10.0, shares, shares) is None


def test_the_difference_recovers_the_injected_noise_where_the_plain_noise_does_not(tmp_path: Path) -> None:
    """Shared texture inflates each half's noise; the difference cancels it."""
    library = _Library(tmp_path)
    result = library.run(keep_halves=True)
    assert result.reason == ""
    folder = Path(library.stack_path).parent
    odd_path = str(folder / "_subsets" / "M99_L_Stacked.half-odd" / "M99_L_Stacked.half-odd.fits")
    even_path = str(folder / "_subsets" / "M99_L_Stacked.half-even" / "M99_L_Stacked.half-even.fits")
    half_noise = INJECTED_FRAME_NOISE / math.sqrt(len(library.stacker.calls[0]))
    # What a structure-free half of this noise reads in the same apertures.
    clean = tmp_path / "clean" / "clean.fits"
    _write(str(clean), half_noise, seed=7, texture=False)
    clean_outcome = measure_stack_noise(
        str(clean), None, str(folder / APERTURE_FILE_NAME), may_choose_apertures=False
    )
    assert clean_outcome.result is not None
    expected = math.sqrt(2.0) * clean_outcome.result.noise_adu
    assert result.difference_noise_stack_unit == pytest.approx(expected, rel=0.3)
    plain = measure_stack_noise(odd_path, None, str(folder / APERTURE_FILE_NAME), may_choose_apertures=False)
    assert plain.result is not None
    # The plain noise of one half is far above the injected noise ...
    assert plain.result.noise_adu > 1.6 * clean_outcome.result.noise_adu
    # ... and the difference sits near sqrt(2) times it, not near that.
    assert result.difference_noise_stack_unit < plain.result.noise_adu
    direct = measure_difference_noise(
        odd_path, even_path, library.reference, str(folder / APERTURE_FILE_NAME)
    )
    assert direct.result is not None
    assert direct.result.noise_adu == pytest.approx(result.difference_noise_stack_unit)


def test_the_rows_sit_on_the_library_stack_beside_its_own_noise(tmp_path: Path) -> None:
    """Nine stack-level rows are stored on the library stack's subject."""
    library = _Library(tmp_path)
    library.record_library_count(float(FRAME_COUNT))
    result = library.run()
    assert result.rows_written == 9
    subject_id = f"M 99/{STACK_NAME}"
    for metric in NINE_METRICS:
        rows = library.rows(metric)
        assert len(rows) == 1
        assert rows[0].subject.subject_id == subject_id
        assert rows[0].subject.level == SubjectLevel.STACK
        if metric == STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO:
            # No sky rates are stored here, so the ratio gives a reason.
            assert rows[0].status == MeasurementStatus.NOT_MEASURED
            assert rows[0].reason
        else:
            assert rows[0].status == MeasurementStatus.MEASURED
    flux = library.rows(STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT)[0]
    normalized_row = library.rows(STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED)[0]
    stack_unit_row = library.rows(STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT)[0]
    # The denominator is stored, so the normalized value can be turned back.
    assert flux.value == pytest.approx(result.reference_flux_stack_unit)
    assert stack_unit_row.value / flux.value == pytest.approx(normalized_row.value)
    # The shared frame's two shares and the two counts rebuild the factor.
    odd_share = library.rows(STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION)[0].value
    even_share = library.rows(STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION)[0].value
    odd_count = library.rows(STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT)[0].value
    even_count = library.rows(STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT)[0].value
    factor = library.rows(STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR)[0].value
    assert 1 / odd_count + 1 / even_count - 2 * odd_share * even_share == pytest.approx(factor)
    normalized = library.rows(STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED)[0]
    assert normalized.value == pytest.approx(result.difference_noise_normalized)
    assert normalized.uncertainty is not None
    assert normalized.sample_count == result.aperture_count
    assert "f005.fits" in (normalized.reason or "")
    assert library.rows(STACKING_EMPTY_APERTURE_NOISE_NORMALIZED) == []
    odd_count = library.rows(STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT)[0].value
    assert odd_count == result.odd_half_effective_frame_count == len(library.stacker.calls[0])


def test_the_halves_stack_the_right_frames_and_are_deleted_afterwards(tmp_path: Path) -> None:
    """Each half holds the reference frame; no half stack stays in _subsets."""
    library = _Library(tmp_path)
    folder = Path(library.stack_path).parent
    before = sorted(path.name for path in folder.iterdir())
    result = library.run()
    odd, even = library.stacker.calls
    assert library.reference in odd and library.reference in even
    assert set(odd) & set(even) == {library.reference}
    assert len(odd) + len(even) == FRAME_COUNT + 1
    assert result.odd_half_frames == len(odd)
    assert result.even_half_frames == len(even)
    assert result.shared_frame == library.reference
    assert len(library.slot_entries) == 2
    assert not list((folder / "_subsets").glob("*half*"))
    assert sorted(path.name for path in folder.iterdir() if path.name != "_subsets") == [
        name for name in before if name != "_subsets"
    ]


def test_the_halves_can_be_kept(tmp_path: Path) -> None:
    """With keep_halves the two half stacks stay under _subsets."""
    library = _Library(tmp_path)
    library.run(keep_halves=True)
    kept = sorted(path.name for path in (Path(library.stack_path).parent / "_subsets").iterdir())
    assert kept == ["M99_L_Stacked.half-even", "M99_L_Stacked.half-odd"]


def test_without_stored_library_weights_no_equivalent_is_made_up(tmp_path: Path) -> None:
    """With no stored effective count there is no equivalent."""
    library = _Library(tmp_path)
    result = library.run()
    assert result.full_stack_equivalent_normalized is None
    assert result.difference_noise_normalized is not None


def test_the_equivalent_follows_the_weights_formula_with_the_shared_frame(tmp_path: Path) -> None:
    """With the effective count stored, the equivalent follows the formula."""
    library = _Library(tmp_path)
    library.record_library_count(float(FRAME_COUNT))
    result = library.run()
    # Frame 5 is in the even half by place, so it is added to the odd half:
    # 13 odd and 12 even frames.
    odd_only, even_only = 12, 11
    factor = odd_only / 13**2 + even_only / 12**2 + (1 / 13 - 1 / 12) ** 2
    assert result.difference_noise_normalized is not None
    expected = result.difference_noise_normalized / math.sqrt(FRAME_COUNT * factor)
    assert result.full_stack_equivalent_normalized == pytest.approx(expected)
    # Close to half the difference, the value for equal halves.
    assert result.full_stack_equivalent_normalized == pytest.approx(
        result.difference_noise_normalized / 2.0, rel=0.1
    )


def _assert_not_measured(library: _Library, reason_part: str, result: Any) -> None:
    """Check that a refusal stored nine not-measured rows and made no number.

    Parameters
    ----------
    library : `_Library`
        The test library.
    reason_part : `str`
        Words the reason must hold.
    result : `HalfDifferenceNoiseResult`
        The job's result.
    """
    assert reason_part in result.reason
    assert result.difference_noise_normalized is None
    assert result.full_stack_equivalent_normalized is None
    for metric in NINE_METRICS:
        rows = library.rows(metric)
        assert len(rows) == 1
        assert rows[0].status == MeasurementStatus.NOT_MEASURED
        assert rows[0].value is None
        assert reason_part in (rows[0].reason or "")


def test_too_few_frames_in_a_half_is_not_measured(tmp_path: Path) -> None:
    """Under the minimum per half, nothing is stacked."""
    library = _Library(tmp_path, count=2 * HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF - 4, reference_index=2)
    result = library.run()
    _assert_not_measured(library, f"fewer than the {HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF}", result)
    assert library.stacker.calls == []


def test_a_library_stack_without_a_recorded_reference_is_not_measured(tmp_path: Path) -> None:
    """No recorded registration reference frame: no guess at one."""
    library = _Library(tmp_path, reference_index=None)
    result = library.run()
    _assert_not_measured(library, "registration reference frame is not recorded", result)
    assert library.stacker.calls == []


def test_missing_noise_sums_or_apertures_are_not_measured(tmp_path: Path) -> None:
    """The apertures' identity must be checkable; the apertures must exist."""
    no_sums = _Library(tmp_path / "a", sums=False)
    _assert_not_measured(no_sums, "noise sums were not saved", no_sums.run())
    no_apertures = _Library(tmp_path / "b", apertures=False, sums=False)
    # Without sums the first check fires; give it another stack's sums so
    # the apertures check runs.
    other = _Library(tmp_path / "c")
    sums_file = Path(other.stack_path).with_name(STACK_NAME.replace(".fits", "_noise_sums.json"))
    target_file = Path(no_apertures.stack_path).with_name(sums_file.name)
    target_file.write_text(sums_file.read_text(encoding="utf-8"), encoding="utf-8")
    result = no_apertures.run()
    _assert_not_measured(no_apertures, "no stored noise apertures", result)


def test_apertures_of_another_identity_than_the_library_stack_are_refused(tmp_path: Path) -> None:
    """The library stack's sums must come from the halves' apertures."""
    library = _Library(tmp_path)
    sums_file = Path(library.stack_path).with_name(STACK_NAME.replace(".fits", "_noise_sums.json"))
    body = json.loads(sums_file.read_text(encoding="utf-8"))
    body["aperture_identity"] = "some-other-set"
    sums_file.write_text(json.dumps(body), encoding="utf-8")
    result = library.run()
    _assert_not_measured(library, "other apertures than the halves", result)
    assert not list((Path(library.stack_path).parent / "_subsets").glob("*half*"))


def test_halves_lined_up_with_different_references_are_refused(tmp_path: Path) -> None:
    """A half lined up with another reference frame gives no number."""
    library = _Library(tmp_path, stacker=FakeHalfStacker(obey_request=False))
    result = library.run()
    _assert_not_measured(library, "cannot be registered on one reference", result)
    # The reference was asked for; the guard caught a stacker that ignored it.
    assert library.stacker.requested == [library.reference, library.reference]
    assert not list((Path(library.stack_path).parent / "_subsets").glob("*half*"))


def test_the_library_stacks_reference_is_handed_to_both_halves(tmp_path: Path) -> None:
    """A stacker that would pick its own frame is told which one to use."""
    library = _Library(tmp_path)
    odd, even = None, None
    result = library.run()
    odd, even = library.stacker.calls
    # Without the request the stacker would pick the first frame of each half,
    # and the two halves have different first frames.
    assert odd[0] != even[0]
    assert library.stacker.requested == [library.reference, library.reference]
    assert result.reason == ""
    assert result.difference_noise_normalized is not None


def test_a_running_library_stack_of_the_target_stops_the_job(tmp_path: Path) -> None:
    """While a library stack of the target runs, the job raises."""
    library = _Library(tmp_path)
    with library_stack_guard("M 99"), pytest.raises(ConflictError):
        library.run()
    assert library.stacker.attempts == 0
    assert library.rows(STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED) == []
    # Another target's library stack does not stop it.
    with library_stack_guard("M 100"):
        assert library.run().reason == ""


def test_the_halves_are_deleted_after_a_stacker_failure(tmp_path: Path) -> None:
    """The second half fails after the first was written; no half stays."""
    library = _Library(tmp_path, stacker=FakeHalfStacker(fail_on_call=2))
    result = library.run()
    _assert_not_measured(library, "the stacker failed on the even half", result)
    assert library.stacker.attempts == 2
    assert not list((Path(library.stack_path).parent / "_subsets").glob("*half*"))


def test_a_spectral_stack_is_refused_without_stacking(tmp_path: Path) -> None:
    """Spectral stacks are not measured, and the refusal is stored."""
    library = _Library(tmp_path, frame_fields={"filter": "SPEC"})
    result = library.run()
    _assert_not_measured(library, "imaging stacks only", result)
    assert library.stacker.attempts == 0


def test_the_job_stores_the_model_variance_ratio_from_each_frames_own_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With stored sky rates the job stores R and the model equivalent."""
    library = _Library(tmp_path)
    library.record_library_count(float(FRAME_COUNT))

    def inputs(members: list[Any], weights: Any, *_arguments: Any, **_keywords: Any) -> list[FrameNoiseInput]:
        """Give each frame a sky that grows through the night.

        Returns
        -------
        inputs : `list` [`FrameNoiseInput`]
            One entry for each member.
        """
        return [
            FrameNoiseInput(1.0, 100.0 + 40.0 * index, 3.8, 0.783) for index, _member in enumerate(members)
        ]

    monkeypatch.setattr(subset_noise_run, "frame_noise_inputs_from_store", inputs)
    result = library.run()
    assert result.model_variance_ratio is not None
    members = sorted(library.target.frames, key=lambda frame: frame.timestamp)
    variances = {
        frame.path: (100.0 + 40.0 * index + 3.8**2) / 0.783**2 for index, frame in enumerate(members)
    }
    # The fake inputs follow the library's own member list.
    ordered = {frame.path: variances[frame.path] for frame in members}
    odd, even = library.stacker.calls
    odd_shares = {path: 1 / len(odd) for path in odd}
    even_shares = {path: 1 / len(even) for path in even}
    library_shares = {frame.path: 1 / FRAME_COUNT for frame in members}
    expected = model_variance_ratio(odd_shares, even_shares, library_shares, ordered)
    assert result.model_variance_ratio == pytest.approx(expected)
    assert library.rows(STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO)[0].value == pytest.approx(expected)
    assert result.full_stack_equivalent_model_normalized == pytest.approx(
        result.difference_noise_normalized / math.sqrt(expected)
    )


def test_zero_pixels_inside_the_aperture_or_in_either_stack_are_kept_out() -> None:
    """A circle that holds an uncovered (exactly zero) pixel gives no sum."""
    plane = np.full((60, 60), 100.0)
    rng = np.random.default_rng(3)
    plane += rng.normal(0.0, 1.0, plane.shape)
    assert signed_aperture_sum(plane, 30.0, 30.0) is not None
    # One zero pixel inside the circle, none in the ring (it starts at 7 px).
    inside = plane.copy()
    inside[30, 31] = 0.0
    assert signed_aperture_sum(inside, 30.0, 30.0) is None
    # One zero pixel in the ring.
    ring = plane.copy()
    ring[30, 38] = 0.0
    assert signed_aperture_sum(ring, 30.0, 30.0) is None


def test_the_difference_leaves_out_apertures_that_touch_a_zero_edge_of_either_stack(tmp_path: Path) -> None:
    """Pixels that either stack does not cover are masked out."""
    library = _Library(tmp_path)
    library.run(keep_halves=True)
    folder = Path(library.stack_path).parent
    odd_path = folder / "_subsets" / "M99_L_Stacked.half-odd" / "M99_L_Stacked.half-odd.fits"
    even_path = folder / "_subsets" / "M99_L_Stacked.half-even" / "M99_L_Stacked.half-even.fits"
    apertures = str(folder / APERTURE_FILE_NAME)
    whole = measure_difference_noise(str(odd_path), str(even_path), library.reference, apertures)
    assert whole.result is not None
    for name, covered in (("odd", odd_path), ("even", even_path)):
        with fits.open(covered) as handle:
            data = np.array(handle[0].data, dtype=np.float32)
            header = handle[0].header
        data[:, : SIZE // 2] = 0.0
        edge = tmp_path / f"edge_{name}.fits"
        fits.PrimaryHDU(data, header=header).writeto(edge)
        other = even_path if name == "odd" else odd_path
        first, second = (str(edge), str(other)) if name == "odd" else (str(other), str(edge))
        outcome = measure_difference_noise(first, second, library.reference, apertures)
        if outcome.result is not None:
            assert outcome.result.aperture_count < whole.result.aperture_count
            sums = np.asarray(outcome.result.aperture_sums)
            assert np.isnan(sums).sum() > 0
        else:
            assert "stored apertures fall on this stack" in outcome.reason


def _synthetic_frames(
    frame_count: int, pixel_count: int, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build frames of independent noise with a sky that changes.

    Parameters
    ----------
    frame_count : `int`
        The number of frames.
    pixel_count : `int`
        Pixels in each frame (a flat image).
    seed : `int`
        Seed of the noise.

    Returns
    -------
    noise_images : `numpy.ndarray`
        The noise of each frame, shape ``(frame_count, pixel_count)``.
    variances : `numpy.ndarray`
        Each frame's noise variance in ADU squared.
    weights : `numpy.ndarray`
        Star-width-like weights from 0.1 to 1 that do not follow the noise.
    """
    generator = np.random.default_rng(seed)
    sky_electrons = np.linspace(80.0, 400.0, frame_count)
    variances = (sky_electrons + 3.8**2) / 0.783**2
    weights = generator.uniform(0.1, 1.0, frame_count)
    noise_images = generator.normal(0.0, 1.0, (frame_count, pixel_count)) * np.sqrt(variances)[:, None]
    return noise_images, variances, weights


def test_the_implied_noise_follows_the_formulas_on_unequal_frames() -> None:
    """Injection: unequal frame noise, weights and a shared frame.

    The truth is the noise of the weighted mean of all frames, measured on the
    image. Both implied noises are checked against it: the one that gives
    every frame the same noise (E = D / sqrt(N_eff x F)) and the one with each
    frame's own variance (E = D / sqrt(R)). The bound is derived: a standard
    deviation of M pixels has a relative error of 1 / sqrt(2 M); the truth
    and the difference share their noise images, so the ratio's error is at
    most the two added, and the bound allows three times that (3 sigma). The
    alternating split puts frames of every sky in both halves, so the
    equal-noise formula is expected to stay within 2% here; the larger gap
    that the model version closes needs halves that differ in noise.
    """
    frame_count, pixel_count = 40, 400_000
    noise_images, variances, weights = _synthetic_frames(frame_count, pixel_count, seed=11)
    paths = [f"f{index}" for index in range(frame_count)]
    shared = 17
    odd_indices = sorted({*range(0, frame_count, 2), shared})
    even_indices = sorted({*range(1, frame_count, 2), shared})

    def shares(indices: list[int]) -> dict[str, float]:
        """Normalize the weights of some frames.

        Returns
        -------
        shares : `dict` [`str`, `float`]
            Weight share by frame path.
        """
        total = weights[indices].sum()
        return {paths[index]: float(weights[index] / total) for index in indices}

    odd_shares, even_shares = shares(odd_indices), shares(even_indices)
    library_shares = shares(list(range(frame_count)))

    def combine(share_map: dict[str, float]) -> np.ndarray:
        """Take the weighted mean of the frames' noise.

        Returns
        -------
        image : `numpy.ndarray`
            The stack's noise.
        """
        return sum(share * noise_images[paths.index(path)] for path, share in share_map.items())

    difference = float(np.std(combine(odd_shares) - combine(even_shares)))
    truth = float(np.std(combine(library_shares)))
    model_truth = math.sqrt(
        sum(library_shares[path] ** 2 * variances[index] for index, path in enumerate(paths))
    )
    variance_by_path = {path: float(variances[index]) for index, path in enumerate(paths)}
    effective = effective_frame_count(list(library_shares.values()))
    implied_equal = full_stack_noise_equivalent(difference, effective, odd_shares, even_shares)
    ratio = model_variance_ratio(odd_shares, even_shares, library_shares, variance_by_path)
    implied_model = full_stack_noise_equivalent_from_model(difference, ratio)
    assert implied_equal is not None and implied_model is not None
    bound = 3.0 * 2.0 / math.sqrt(2.0 * pixel_count)
    # The injected truth agrees with the formula for the weighted mean's noise.
    assert truth == pytest.approx(model_truth, rel=bound)
    # With each frame's own variance the implied noise is the truth.
    assert implied_model == pytest.approx(truth, rel=bound)
    # With one variance for all frames it is close too (alternating split).
    assert abs(implied_equal / truth - 1.0) < 0.02


def test_a_stacker_failure_is_not_measured_and_the_run_does_not_raise(tmp_path: Path) -> None:
    """A Siril failure stores the reason and never falls back to a number."""
    library = _Library(tmp_path, stacker=FakeHalfStacker(fail=True))
    result = library.run()
    _assert_not_measured(library, "the stacker failed on the odd half", result)


def test_the_library_stack_and_the_target_are_left_alone(tmp_path: Path) -> None:
    """Only _subsets changes in the stack's folder; the target is untouched."""
    library = _Library(tmp_path)
    folder = Path(library.stack_path).parent
    before = {
        str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()
    }
    target_before = library.target.model_dump()
    library.run()
    after = {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    assert after == before
    assert library.target.model_dump() == target_before


def test_without_a_context_nothing_is_stored(tmp_path: Path) -> None:
    """A run with no context still measures and stores no rows."""
    library = _Library(tmp_path)
    result = library.run(context=None)
    assert result.reason == ""
    assert result.rows_written == 0
    assert result.difference_noise_normalized is not None
    assert library.rows(STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED) == []


def test_two_stacks_on_different_grids_are_refused(tmp_path: Path) -> None:
    """The difference needs one pixel grid."""
    small = tmp_path / "small.fits"
    big = tmp_path / "big.fits"
    fits.PrimaryHDU(np.ones((50, 50), dtype=np.float32), header=_header()).writeto(small)
    fits.PrimaryHDU(np.ones((60, 60), dtype=np.float32), header=_header()).writeto(big)
    outcome = measure_difference_noise(str(small), str(big), None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert "different pixel grids" in outcome.reason
