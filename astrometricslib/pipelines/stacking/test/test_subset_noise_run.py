"""Tests for the subset stacks: noise growth and each night's share.

A fake stacking program writes a synthetic stack whose noise falls as one over
the square root of the number of frames it was given. The tests check that the
subset path leaves the target and the library stack's folder alone, that the
growth series and the leave-one-night-out stacks store the right rows, that the
nights' shares add up to one, and that missing inputs give a clear reason
instead of a guess.
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

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.stacking_driver import StackingDriver, StackRunResult, StackSettings
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EFFECTIVE_FRAME_COUNT,
    STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
    STACKING_FRAME_WEIGHT,
    STACKING_METRICS,
    STACKING_NIGHT_WEIGHT_SHARE,
    STACKING_NOISE_WITHOUT_NIGHT,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    APERTURE_FILE_NAME,
    measure_stack_noise,
)
from astrometricslib.pipelines.stacking.subset_noise_run import (
    GrowthStep,
    frames_by_night,
    night_weight_shares,
    plan_growth_steps,
    run_night_contributions,
    run_noise_growth,
    stack_chosen_subset,
)
from astrometricslib.pipelines.stacking.subset_stack import (
    MINIMUM_SUBSET_FRAMES,
    check_subset_label,
    stack_frame_subset,
    subset_stack_path,
)
from astrometricslib.utilities.observing_night import observing_night_id

SIZE = 900
SKY_LEVEL = 100.0
STAR_POSITIONS = [(150 + 90 * column, 200 + 110 * row) for column in range(8) for row in range(6)]
STACK_NAME = "M99_L_Stacked.fits"
SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}
RUN_TIME = datetime(2026, 10, 23, tzinfo=UTC)


def _sky_header() -> fits.Header:
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


def _write_stack(path: str, noise_sigma: float, seed: int) -> None:
    """Write a synthetic stack: flat sky, white noise and round stars.

    Parameters
    ----------
    path : `str`
        Where to write.
    noise_sigma : `float`
        The per-pixel noise.
    seed : `int`
        Seed of the noise.
    """
    generator = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = SKY_LEVEL + generator.normal(0.0, noise_sigma, (SIZE, SIZE))
    amplitudes = np.linspace(0.3, 1.0, len(STAR_POSITIONS)) * 2000.0
    for (x, y), amplitude in zip(STAR_POSITIONS, amplitudes, strict=True):
        image += amplitude * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * 2.0**2))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(image.astype(np.float32), header=_sky_header()).writeto(path, overwrite=True)


class FakeStacker(StackingDriver):
    """A stacking program whose stack noise falls with the frame count.

    Attributes
    ----------
    calls : `list` [`list` [`str`]]
        The frame paths of each `stack_batch` call.
    output_files : `list` [`str`]
        The output file of each call.
    """

    name = "Fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.output_files: list[str] = []

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
        """Write a stack with noise 6 / sqrt(frame count) at the output path.

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
            Unused.
        registration : `str` or `None`, optional
            Unused.
        job_id : `str` or `None`, optional
            Unused.
        steps : `PipelineSteps` or `None`, optional
            Unused.

        Returns
        -------
        result : `StackRunResult`
            The output path and empty diagnostics.
        """
        self.calls.append([frame["path"] for frame in frames])
        self.output_files.append(output_file)
        _write_stack(output_file, 6.0 / math.sqrt(len(frames)), seed=len(self.calls))
        return StackRunResult(stacked_path=output_file, diagnostics={}, engine_name=self.name)


def _configuration(tmp_path: Path) -> SimpleNamespace:
    """Build the settings the subset code reads.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Holds the quality database.

    Returns
    -------
    configuration : `SimpleNamespace`
        An object with the getters the subset code calls.
    """
    return SimpleNamespace(
        get_library_path=lambda: str(tmp_path / "library"),
        get_stack_weight=lambda: None,
        get_trim_noisy_stack_edges_enabled=lambda: False,
    )


def _frames(nights: int, per_night: int = 5) -> list[FrameRecord]:
    """Build frames that span several observing nights.

    Parameters
    ----------
    nights : `int`
        How many nights.
    per_night : `int`, optional
        Frames in each night.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        Frames of 60 s from one camera, taken at 22:00 local time.
    """
    start = datetime(2026, 10, 1, 22, 0).timestamp()
    return [
        FrameRecord(
            path=f"/frames/lights/M 99/Scope/Cam/n{night}_{index:02d}.fits",
            camera="Cam",
            exposure="60",
            timestamp=start + night * 86400.0 + index * 120.0,
        )
        for night in range(nights)
        for index in range(per_night)
    ]


class _Library:
    """A target with a library stack on disk, its record and its apertures.

    Attributes
    ----------
    target : `Target`
        The target.
    stack_path : `str`
        The library stack.
    configuration : `SimpleNamespace`
        The settings.
    drivers : `Drivers`
        Holds the fake stacking program.
    stacker : `FakeStacker`
        The fake stacking program.
    slot_entries : `list` [`int`]
        One entry for each time the stacking slot was entered.
    """

    def __init__(self, tmp_path: Path, nights: int, *, record: bool = True, apertures: bool = True) -> None:
        self.configuration = _configuration(tmp_path)
        frames = _frames(nights)
        self.target = Target(id="M 99", frames=frames)
        folder = tmp_path / "stacks" / "lights" / "M 99"
        self.stack_path = str(folder / STACK_NAME)
        _write_stack(self.stack_path, 6.0 / math.sqrt(len(frames)), seed=99)
        if apertures:
            outcome = measure_stack_noise(self.stack_path, None, str(folder / APERTURE_FILE_NAME))
            assert outcome.result is not None
        if record:
            body = {"fingerprint": "x", "record": {"frames": [[frame.path, 1, 1] for frame in frames]}}
            (folder / "M99_L_Stacked_inputs.json").write_text(json.dumps(body), encoding="utf-8")
        self.stacker = FakeStacker()
        self.drivers = Drivers(stacking=self.stacker)
        self.slot_entries: list[int] = []

    def slot(self) -> _Slot:
        """Give a stacking slot that counts how often it is entered.

        Returns
        -------
        slot : `_Slot`
            A context manager.
        """
        return _Slot(self.slot_entries)

    def context(self, run_id: str = "run-1") -> PipelineRunContext:
        """Build the run context.

        Parameters
        ----------
        run_id : `str`, optional
            The run id.

        Returns
        -------
        context : `PipelineRunContext`
            The context for the target.
        """
        return PipelineRunContext("M 99", "stacking", "1.3.0", run_id, run_id, RUN_TIME)

    def store(self) -> QualityStore:
        """Open the quality store of the settings.

        Returns
        -------
        store : `QualityStore`
            The store.
        """
        return QualityStore.from_configuration(self.configuration)

    def record_library_weights(self, weight_by_night: dict[int, float | None]) -> None:
        """Store the frame weights and effective count of the library stack.

        Parameters
        ----------
        weight_by_night : `dict` [`int`, `float` or `None`]
            The weight of every frame of each night index; `None` for unknown.
        """
        context = self.context("library-run")
        rows: list[QualityMeasurement] = [
            build_measurement(
                SPEC_BY_NAME[STACKING_EFFECTIVE_FRAME_COUNT],
                10.0,
                context,
                context.stack_subject(STACK_NAME),
            )
        ]
        from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id

        for frame in self.target.frames:
            night = int(Path(frame.path).name[1])
            subject = context.stack_subject(STACK_NAME).model_copy(
                update={
                    "level": SubjectLevel.FRAME,
                    "subject_id": frame_subject_id(frame.path, "M 99"),
                    "observing_night_id": observing_night_id(frame.timestamp or 0.0),
                }
            )
            weight = weight_by_night[night]
            rows.append(
                build_measurement(
                    SPEC_BY_NAME[STACKING_FRAME_WEIGHT],
                    weight,
                    context,
                    subject,
                    reason="" if weight is not None else "Siril weighted the frames itself",
                )
            )
        record_measurements(self.store(), rows)


class _Slot:
    """A context manager that counts its entries."""

    def __init__(self, entries: list[int]) -> None:
        self._entries = entries

    def __enter__(self) -> None:
        """Count one entry."""
        self._entries.append(1)

    def __exit__(self, *_exception: object) -> None:
        """Leave without hiding an exception."""


def _folder_listing(folder: Path) -> dict[str, bytes]:
    """Read every file below a folder.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder.

    Returns
    -------
    contents : `dict` [`str`, `bytes`]
        The bytes of each file, by path relative to the folder.
    """
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _rows(library: _Library, metric: str) -> list[QualityMeasurement]:
    """Read the stored rows of one metric of the target.

    Parameters
    ----------
    library : `_Library`
        The test library.
    metric : `str`
        The metric.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The newest row for each subject, oldest first.
    """
    return library.store().query(metric=metric, target_id="M 99")


def _growth(library: _Library, **keywords: Any) -> Any:
    """Run the noise-growth series on a test library.

    Parameters
    ----------
    library : `_Library`
        The test library.
    **keywords
        Replacements for the call's keyword arguments.

    Returns
    -------
    result : `NoiseGrowthResult`
        The series.
    """
    arguments = {
        "context": library.context(),
        "configuration": library.configuration,
        "drivers": library.drivers,
        "stacking_slot": library.slot,
    }
    arguments.update(keywords)
    return run_noise_growth(library.target, library.stack_path, **arguments)


def _nights(library: _Library, **keywords: Any) -> Any:
    """Run the night-contribution job on a test library.

    Parameters
    ----------
    library : `_Library`
        The test library.
    **keywords
        Replacements for the call's keyword arguments.

    Returns
    -------
    result : `NightContributionResult`
        The nights.
    """
    arguments = {
        "context": library.context(),
        "configuration": library.configuration,
        "drivers": library.drivers,
        "stacking_slot": library.slot,
    }
    arguments.update(keywords)
    return run_night_contributions(library.target, library.stack_path, **arguments)


def test_the_growth_series_leaves_the_target_and_the_library_stack_folder_alone(tmp_path: Path) -> None:
    """Only the _subsets folder is added; nothing else changes."""
    library = _Library(tmp_path, nights=3)
    before_target = library.target.model_dump()
    library_folder = Path(library.stack_path).parent
    before_files = _folder_listing(library_folder)
    _growth(library)
    assert library.target.model_dump() == before_target
    after_files = _folder_listing(library_folder)
    changed = {
        name for name in after_files if name not in before_files or after_files[name] != before_files[name]
    }
    assert changed
    assert all(name.startswith("_subsets/") for name in changed)
    assert {name for name in before_files if name not in after_files} == set()
    assert all(path.startswith(str(library_folder / "_subsets")) for path in library.stacker.output_files)


def test_the_growth_series_stores_noise_and_counts_for_each_subset(tmp_path: Path) -> None:
    """Three nights give a one-night and a two-night subset, noisier first."""
    library = _Library(tmp_path, nights=3)
    result = _growth(library)
    assert [subset.frames_submitted for subset in result.subsets] == [5, 10]
    assert [subset.effective_frame_count for subset in result.subsets] == [5.0, 10.0]
    first, second = result.subsets
    assert first.normalized_noise is not None
    assert second.normalized_noise is not None
    assert second.normalized_noise < first.normalized_noise
    assert second.normalized_noise / first.normalized_noise == pytest.approx(1 / math.sqrt(2), rel=0.3)
    rows = _rows(library, STACKING_EMPTY_APERTURE_NOISE_NORMALIZED)
    subset_rows = [row for row in rows if "/_subsets/" in row.subject.subject_id]
    assert len(subset_rows) == 2
    assert all(row.subject.level == SubjectLevel.STACK for row in subset_rows)
    assert all(row.status == MeasurementStatus.MEASURED for row in subset_rows)
    assert all(row.run_id == "run-1" and row.measured_at == RUN_TIME for row in subset_rows)
    assert {row.subject.subject_id.split("/")[2] for row in subset_rows} == {
        "M99_L_Stacked.nights-1",
        "M99_L_Stacked.nights-2",
    }
    counts = _rows(library, STACKING_EFFECTIVE_FRAME_COUNT)
    assert sorted(row.value for row in counts if row.value is not None) == [5.0, 10.0]
    assert len(library.slot_entries) == 2


def test_the_series_uses_the_stored_apertures_of_the_library_stack(tmp_path: Path) -> None:
    """No apertures file is made inside a subset folder."""
    library = _Library(tmp_path, nights=3)
    _growth(library)
    created = list(Path(library.stack_path).parent.rglob(APERTURE_FILE_NAME))
    assert created == [Path(library.stack_path).parent / APERTURE_FILE_NAME]


def test_a_single_night_uses_fractions_of_its_frames(tmp_path: Path) -> None:
    """One night of 16 frames gives a quarter, a half and three quarters."""
    library = _Library(tmp_path, nights=1, record=False)
    frames = _frames(1, per_night=16)
    library.target = Target(id="M 99", frames=frames)
    body = {"fingerprint": "x", "record": {"frames": [[frame.path, 1, 1] for frame in frames]}}
    (Path(library.stack_path).parent / "M99_L_Stacked_inputs.json").write_text(
        json.dumps(body), encoding="utf-8"
    )
    result = _growth(library)
    assert [subset.frames_submitted for subset in result.subsets] == [4, 8, 12]
    assert [subset.label.split(".")[-1] for subset in result.subsets] == [
        "fraction-25",
        "fraction-50",
        "fraction-75",
    ]


def test_a_missing_input_record_is_an_error_not_a_guess(tmp_path: Path) -> None:
    """Without _inputs.json the library stack's frames are not known."""
    library = _Library(tmp_path, nights=3, record=False)
    with pytest.raises(NotFoundError, match="record of its input frames"):
        _growth(library)
    with pytest.raises(NotFoundError, match="record of its input frames"):
        _nights(library)
    assert library.stacker.calls == []


def test_missing_apertures_are_reported_and_not_created_from_a_subset(tmp_path: Path) -> None:
    """A target with no stored apertures gets not-measured noise."""
    library = _Library(tmp_path, nights=3, apertures=False)
    result = _growth(library)
    assert all(subset.normalized_noise is None for subset in result.subsets)
    assert all("no stored noise apertures" in subset.noise_reason for subset in result.subsets)
    assert not list(Path(library.stack_path).parent.rglob(APERTURE_FILE_NAME))
    rows = _rows(library, STACKING_EMPTY_APERTURE_NOISE_NORMALIZED)
    assert rows
    assert all(row.status == MeasurementStatus.NOT_MEASURED and row.reason for row in rows)


def test_night_contributions_store_shares_that_add_up_to_one(tmp_path: Path) -> None:
    """Each night gets a share and the noise of the stack without it."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 2.0, 2: 1.0})
    result = _nights(library)
    shares = {entry.night_id: entry.weight_share for entry in result.nights}
    assert sorted(shares.values()) == pytest.approx([0.25, 0.25, 0.5])
    assert sum(value for value in shares.values() if value is not None) == pytest.approx(1.0)
    assert [entry.frame_count for entry in result.nights] == [5, 5, 5]
    assert all(entry.subset is not None and entry.subset.frames_submitted == 10 for entry in result.nights)
    share_rows = _rows(library, STACKING_NIGHT_WEIGHT_SHARE)
    assert sorted(row.subject.observing_night_id for row in share_rows) == sorted(shares)
    assert all(
        "/_subsets/" in row.subject.subject_id and "without-" in row.subject.subject_id for row in share_rows
    )
    assert sum(row.value for row in share_rows if row.value is not None) == pytest.approx(1.0)
    noise_rows = _rows(library, STACKING_NOISE_WITHOUT_NIGHT)
    assert len(noise_rows) == 3
    assert all(row.status == MeasurementStatus.MEASURED and row.value for row in noise_rows)
    assert all(row.subject.level == SubjectLevel.STACK for row in noise_rows)
    first_night_frames = library.stacker.calls[0]
    assert not any("n0_" in path for path in first_night_frames)
    assert len(library.slot_entries) == 3


def test_unknown_library_weights_give_not_measured_shares_but_measured_noise(tmp_path: Path) -> None:
    """Equal weights are never assumed when the stacker did not report them."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: None, 1: None, 2: None})
    result = _nights(library)
    assert all(entry.weight_share is None for entry in result.nights)
    assert all("unknown" in entry.weight_reason for entry in result.nights)
    share_rows = _rows(library, STACKING_NIGHT_WEIGHT_SHARE)
    assert len(share_rows) == 3
    assert all(row.status == MeasurementStatus.NOT_MEASURED and "unknown" in row.reason for row in share_rows)
    assert all(row.value for row in _rows(library, STACKING_NOISE_WITHOUT_NIGHT))


def test_a_stack_with_no_recorded_weights_says_so(tmp_path: Path) -> None:
    """A stack made before weights were recorded gets a clear reason."""
    library = _Library(tmp_path, nights=3)
    result = _nights(library)
    assert all("no stored frame weights" in entry.weight_reason for entry in result.nights)


def test_one_night_has_nothing_to_leave_out(tmp_path: Path) -> None:
    """A stack of one night makes no leave-out stack and says why."""
    library = _Library(tmp_path, nights=1)
    result = _nights(library)
    assert result.nights == []
    assert any("fewer than two observing nights" in note for note in result.notes)
    assert library.stacker.calls == []


def test_a_night_that_leaves_too_few_frames_is_recorded_not_stacked(tmp_path: Path) -> None:
    """Leaving out the night of five frames leaves too few."""
    library = _Library(tmp_path, nights=2)
    frames = _frames(2, per_night=5)[:7]
    library.target = Target(id="M 99", frames=frames)
    body = {"fingerprint": "x", "record": {"frames": [[frame.path, 1, 1] for frame in frames]}}
    (Path(library.stack_path).parent / "M99_L_Stacked_inputs.json").write_text(
        json.dumps(body), encoding="utf-8"
    )
    result = _nights(library)
    first, second = result.nights
    assert first.frame_count == 5
    assert first.subset is None
    assert second.subset is not None
    assert second.subset.frames_submitted == 5
    assert any("only 2 frame(s)" in note for note in result.notes)
    rows = {row.subject.observing_night_id: row for row in _rows(library, STACKING_NOISE_WITHOUT_NIGHT)}
    assert rows[first.night_id].status == MeasurementStatus.NOT_MEASURED
    assert "fewer than" in rows[first.night_id].reason
    assert rows[second.night_id].status == MeasurementStatus.MEASURED
    assert len(library.stacker.calls) == 1


def test_the_growth_job_can_include_the_night_contributions_only_when_asked(tmp_path: Path) -> None:
    """The growth call never makes leave-out stacks by itself."""
    library = _Library(tmp_path, nights=3)
    _growth(library)
    assert _rows(library, STACKING_NIGHT_WEIGHT_SHARE) == []
    assert not any("without-" in path for path in library.stacker.output_files)


def test_stack_chosen_subset_writes_beside_the_library_stack_or_to_the_folder_asked(tmp_path: Path) -> None:
    """The default folder is _subsets/<label>; a given folder is used."""
    library = _Library(tmp_path, nights=3)
    frames = library.target.frames[:6]
    default = stack_chosen_subset(
        library.target,
        frames,
        "mine",
        library.stack_path,
        context=library.context(),
        configuration=library.configuration,
        drivers=library.drivers,
        stacking_slot=library.slot,
    )
    assert default.stack_path == str(Path(library.stack_path).parent / "_subsets" / "mine" / "mine.fits")
    elsewhere = stack_chosen_subset(
        library.target,
        frames,
        "mine",
        library.stack_path,
        context=None,
        configuration=library.configuration,
        drivers=library.drivers,
        stacking_slot=library.slot,
        output_folder=str(tmp_path / "scratch"),
    )
    assert elsewhere.stack_path == str(tmp_path / "scratch" / "mine.fits")
    assert elsewhere.rows_written == 0
    assert Path(library.stack_path).exists()


def test_a_subset_needs_enough_frames_and_a_safe_label(tmp_path: Path) -> None:
    """Too few frames or an unsafe label are refused before any stacking."""
    library = _Library(tmp_path, nights=1)
    few = library.target.frames[: MINIMUM_SUBSET_FRAMES - 1]
    with pytest.raises(InvalidArgumentError, match="at least"):
        stack_frame_subset(
            "M 99",
            few,
            str(tmp_path / "x.fits"),
            configuration=library.configuration,
            drivers=library.drivers,
        )
    with pytest.raises(InvalidArgumentError, match="absolute"):
        stack_frame_subset(
            "M 99",
            library.target.frames,
            "x.fits",
            configuration=library.configuration,
            drivers=library.drivers,
        )
    for bad in ("", ".hidden", "..", "a/b", "a b"):
        with pytest.raises(InvalidArgumentError, match="label"):
            check_subset_label(bad)
    with pytest.raises(InvalidArgumentError, match="label"):
        subset_stack_path("/stacks", "../escape", "x.fits")
    assert library.stacker.calls == []


def test_the_subset_never_changes_the_frame_records_it_is_given(tmp_path: Path) -> None:
    """The stacker works on copies of the frames."""
    library = _Library(tmp_path, nights=1)
    before = [frame.model_dump() for frame in library.target.frames]
    stack_frame_subset(
        "M 99",
        library.target.frames,
        str(tmp_path / "scratch" / "x.fits"),
        configuration=library.configuration,
        drivers=library.drivers,
    )
    assert [frame.model_dump() for frame in library.target.frames] == before


def test_growth_steps_are_spread_and_skip_small_subsets() -> None:
    """Many nights are thinned evenly; subsets under four frames drop."""
    frames = _frames(8, per_night=5)
    nights, undated = frames_by_night(frames)
    assert undated == []
    steps = plan_growth_steps(nights, maximum_steps=3)
    assert [step.key for step in steps] == ["nights-1", "nights-4", "nights-7"]
    assert [len(step.frames) for step in steps] == [5, 20, 35]
    small = {night: group[:2] for night, group in nights.items()}
    assert [step.key for step in plan_growth_steps(small, maximum_steps=10)] == [
        "nights-2",
        "nights-3",
        "nights-4",
        "nights-5",
        "nights-6",
        "nights-7",
    ]
    with pytest.raises(InvalidArgumentError, match="maximum_steps"):
        plan_growth_steps(nights, maximum_steps=0)
    assert plan_growth_steps({}, 4) == []
    assert isinstance(steps[0], GrowthStep)


def test_frames_without_a_timestamp_belong_to_no_night() -> None:
    """They are returned apart from the nights."""
    frames = [*_frames(2, per_night=2), FrameRecord(path="/x/lights/M 99/a/b/z.fits", camera="Cam")]
    nights, undated = frames_by_night(frames)
    assert len(nights) == 2
    assert [frame.path for frame in undated] == ["/x/lights/M 99/a/b/z.fits"]


def test_night_weight_shares_add_up_and_refuse_unknown_weights() -> None:
    """Shares come from summed weights; unknown weights give a reason."""
    shares, reason = night_weight_shares([("a", 1.0), ("a", 1.0), ("b", 2.0)])
    assert reason == ""
    assert shares == pytest.approx({"a": 0.5, "b": 0.5})
    assert night_weight_shares([("a", None), ("b", 1.0)])[1]
    assert night_weight_shares([])[1]
    assert night_weight_shares([(None, 1.0)])[1]
    assert night_weight_shares([("a", 0.0)])[1]
