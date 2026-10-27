"""Tests for the subset stacks: noise growth and each night's share.

A fake stacking program writes a synthetic stack whose noise falls as one over
the square root of the number of frames it was given. The tests check that the
subset path leaves the target and the library stack's folder alone, that the
growth series and the leave-one-night-out stacks store the right rows, that the
nights' shares add up to one, and that missing inputs give a clear reason
instead of a guess.
"""

import contextvars
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
from astrometricslib.models.quality.subset_stacks import EARLIER_NIGHTS_VARIANT
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
from astrometricslib.pipelines.stacking import subset_noise_run
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_APERTURE_SET_NUMBER,
    STACKING_EFFECTIVE_FRAME_COUNT,
    STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
    STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_FRAME_WEIGHT,
    STACKING_GROUP_EXPOSURE_S,
    STACKING_GROUP_FRAMES_STACKED,
    STACKING_GROUP_GAIN_APPLIED,
    STACKING_GROUP_REFERENCE_EXPOSURE_S,
    STACKING_METRICS,
    STACKING_NIGHT_WEIGHT_SHARE,
    STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT,
    STACKING_NOISE_WITHOUT_NIGHT,
    STACKING_NORMALIZATION_SCALE,
    STACKING_SUBSET_EXPECTED_NOISE_RATIO,
)
from astrometricslib.pipelines.stacking.post_processing import record_science
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    APERTURE_FILE_NAME,
    aperture_identity_to_number,
    load_apertures_with_reason,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import FrameNoiseInput
from astrometricslib.pipelines.stacking.post_processing.stack_noise import measure_stack_noise
from astrometricslib.pipelines.stacking.subset_noise_run import (
    GrowthStep,
    expected_noise_ratio,
    frames_by_night,
    model_variance_ratio,
    night_weight_shares,
    plan_growth_steps,
    run_night_contributions,
    run_noise_growth,
    stack_chosen_subset,
)
from astrometricslib.pipelines.stacking.subset_stack import (
    MINIMUM_SUBSET_FRAMES,
    SubsetStackOutcome,
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
        get_siril_executable=lambda: None,
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
    """The default folder is _subsets/<label>; a folder inside it works."""
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
        output_folder=str(Path(library.stack_path).parent / "_subsets" / "scratch"),
    )
    assert elsewhere.stack_path == str(Path(library.stack_path).parent / "_subsets" / "scratch" / "mine.fits")
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


def _subset_arguments(library: _Library, **keywords: Any) -> dict[str, Any]:
    """Build the keyword arguments of `stack_chosen_subset` for a test library.

    Parameters
    ----------
    library : `_Library`
        The test library.
    **keywords
        Replacements.

    Returns
    -------
    arguments : `dict`
        The keywords.
    """
    arguments = {
        "context": library.context(),
        "configuration": library.configuration,
        "drivers": library.drivers,
        "stacking_slot": library.slot,
    }
    arguments.update(keywords)
    return arguments


def test_an_output_folder_outside_the_subsets_folder_is_refused(tmp_path: Path) -> None:
    """A subset stack can never be written among the library's own files."""
    library = _Library(tmp_path, nights=3)
    frames = library.target.frames[:6]
    library_folder = Path(library.stack_path).parent
    for outside in (tmp_path / "scratch", library_folder, library_folder / "_subsets" / ".." / "x"):
        with pytest.raises(InvalidArgumentError, match="inside"):
            stack_chosen_subset(
                library.target,
                frames,
                "mine",
                library.stack_path,
                **_subset_arguments(library, output_folder=str(outside)),
            )
    assert library.stacker.calls == []


def test_the_work_folder_name_holds_the_subsets_label(tmp_path: Path) -> None:
    """Siril's work folder name holds the label."""
    from astrometricslib.drivers.siril_interface import work_directory_name

    library = _Library(tmp_path, nights=3)
    result = stack_chosen_subset(
        library.target,
        library.target.frames[:6],
        "mine",
        library.stack_path,
        **_subset_arguments(library),
    )
    assert work_directory_name("M 99", result.stack_path) == "M_99__mine"
    assert work_directory_name("M 99", library.stack_path) != "M_99__mine"


def test_a_second_subset_job_or_a_library_stack_of_the_target_is_refused(tmp_path: Path) -> None:
    """One subset job per target, and none beside a library stack."""
    from astrometricslib.foundation.errors import ConflictError
    from astrometricslib.pipelines.stacking.target_stack_guard import library_stack_guard, subset_job_guard

    library = _Library(tmp_path, nights=3)
    with subset_job_guard("M 99"):
        # Another job has its own context, so the guard is not reused.
        with pytest.raises(ConflictError, match="subset-stack job"):
            contextvars.Context().run(_growth, library)
        with pytest.raises(ConflictError, match="subset-stack job"):
            with library_stack_guard("M 99"):
                pass
        # Another target is free.
        with library_stack_guard("M 100"):
            pass
    with library_stack_guard("M 99"):
        # Library stacks of one target do not block each other.
        with library_stack_guard("M 99"):
            with pytest.raises(ConflictError, match="library stack"):
                with subset_job_guard("M 99"):
                    pass
    # Free again afterwards.
    with subset_job_guard("M 99"):
        pass
    assert library.stacker.calls == []


def test_one_failing_subset_is_recorded_as_failed_and_the_series_goes_on(tmp_path: Path) -> None:
    """A stacker error in one step gives not-measured rows and a note."""
    library = _Library(tmp_path, nights=4)
    original = library.stacker.stack_batch

    def run(*arguments: Any, **keywords: Any) -> Any:
        """Fail on the first call only.

        Returns
        -------
        result : `Any`
            What the fake stacker returns.

        Raises
        ------
        RuntimeError
            On the first call.
        """
        if not library.stacker.calls:
            library.stacker.calls.append("failed")
            raise RuntimeError("Siril exploded")
        return original(*arguments, **keywords)

    library.stacker.stack_batch = run  # type: ignore[method-assign]
    result = _growth(library)
    assert len(result.subsets) == 3
    assert "RuntimeError: Siril exploded" in result.subsets[0].stack_error
    assert result.subsets[0].stack_path is None
    assert all(subset.stack_path for subset in result.subsets[1:])
    assert any("failed and was skipped" in note for note in result.notes)
    failed_rows = [
        row
        for row in _rows(library, STACKING_EMPTY_APERTURE_NOISE_NORMALIZED)
        if row.subject.variant == result.subsets[0].label
    ]
    assert failed_rows and all(row.status == MeasurementStatus.NOT_MEASURED for row in failed_rows)
    assert "Siril exploded" in failed_rows[0].reason


def test_every_subset_row_carries_the_variant_field(tmp_path: Path) -> None:
    """Subset rows have the label as variant; library rows have none."""
    library = _Library(tmp_path, nights=3)
    result = _growth(library)
    rows = _rows(library, STACKING_EMPTY_APERTURE_NOISE_NORMALIZED)
    assert {row.subject.variant for row in rows} == {subset.label for subset in result.subsets}
    assert all("/_subsets/" in row.subject.subject_id for row in rows)
    assert library.store().query(library_only=True) == []


def test_members_come_from_the_stored_frame_rows_not_the_inputs_file(tmp_path: Path) -> None:
    """A frame the run did not keep is left out, whatever the inputs say."""
    from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
    from astrometricslib.pipelines.stacking.subset_noise_run import read_library_run

    library = _Library(tmp_path, nights=3)
    context = library.context("library-run")
    rows = [
        build_measurement(
            SPEC_BY_NAME[STACKING_EFFECTIVE_FRAME_COUNT], 10.0, context, context.stack_subject(STACK_NAME)
        )
    ]
    for frame in library.target.frames[1:]:
        subject = context.stack_subject(STACK_NAME).model_copy(
            update={"level": SubjectLevel.FRAME, "subject_id": frame_subject_id(frame.path, "M 99")}
        )
        rows.append(build_measurement(SPEC_BY_NAME[STACKING_FRAME_WEIGHT], 1.0, context, subject))
    record_measurements(library.store(), rows)
    run = read_library_run(library.target, library.stack_path, library.configuration)
    assert [frame.path for frame in run.members] == [frame.path for frame in library.target.frames[1:]]
    assert not any("_inputs.json" in note for note in run.notes)
    # With no stored rows the inputs file is used, and a note says so.
    other = _Library(tmp_path / "other", nights=3)
    fallback = read_library_run(other.target, other.stack_path, other.configuration)
    assert len(fallback.members) == len(other.target.frames)
    assert any("_inputs.json" in note for note in fallback.notes)


def test_a_single_night_series_takes_frames_evenly_through_the_night() -> None:
    """The subsets interleave: every fourth frame, not the first quarter."""
    frames = _frames(1, per_night=16)
    nights, _ = frames_by_night(frames)
    steps = plan_growth_steps(nights)
    quarter = steps[0].frames
    positions = [frames.index(frame) for frame in quarter]
    assert positions == [2, 6, 10, 14]
    half = [frames.index(frame) for frame in steps[1].frames]
    assert half[0] < 4 and half[-1] > 11


def test_the_expected_noise_ratio_uses_each_subsets_own_effective_count() -> None:
    """The ratio is sqrt(N_library / N_subset); unknown counts give none."""
    assert expected_noise_ratio(16.0, 4.0) == pytest.approx(2.0)
    assert expected_noise_ratio(None, 4.0) is None
    assert expected_noise_ratio(16.0, None) is None
    assert expected_noise_ratio(16.0, 0.0) is None


def test_each_subset_stores_its_expected_ratio_and_assumed_weights_make_it_assumed(tmp_path: Path) -> None:
    """Every subset gets a ratio row."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    result = _growth(library)
    rows = _rows(library, STACKING_SUBSET_EXPECTED_NOISE_RATIO)
    assert len(rows) == len(result.subsets)
    assert all(row.status == MeasurementStatus.MEASURED and row.value >= 1.0 for row in rows)
    assert all(subset.expected_noise_ratio is not None for subset in result.subsets)


def test_the_night_difference_is_paired_with_the_library_stacks_sums(tmp_path: Path) -> None:
    """With the library's sums saved, the difference has a paired error."""
    from astrometricslib.pipelines.stacking.post_processing.difference_noise import save_noise_sums

    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    outcome = measure_stack_noise(
        library.stack_path, None, str(Path(library.stack_path).parent / APERTURE_FILE_NAME)
    )
    assert outcome.result is not None
    save_noise_sums(library.stack_path, outcome.result)
    _nights(library)
    rows = _rows(library, STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT)
    assert len(rows) == 3
    assert all(row.status == MeasurementStatus.MEASURED and row.uncertainty is not None for row in rows)
    assert all(row.subject.observing_night_id and row.subject.variant for row in rows)


def test_without_saved_library_sums_the_night_difference_says_so(tmp_path: Path) -> None:
    """No saved sums means a not-measured difference with the reason."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    _nights(library)
    rows = _rows(library, STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT)
    assert rows and all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert "per-aperture sums were not saved" in rows[0].reason


def test_a_night_share_built_from_assumed_weights_is_assumed(tmp_path: Path) -> None:
    """When the library stack's weight rows are assumed, so is the share."""
    library = _Library(tmp_path, nights=3)
    context = library.context("library-run")
    from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id

    rows = [
        build_measurement(
            SPEC_BY_NAME[STACKING_EFFECTIVE_FRAME_COUNT], 10.0, context, context.stack_subject(STACK_NAME)
        )
    ]
    for frame in library.target.frames:
        subject = context.stack_subject(STACK_NAME).model_copy(
            update={
                "level": SubjectLevel.FRAME,
                "subject_id": frame_subject_id(frame.path, "M 99"),
                "observing_night_id": observing_night_id(frame.timestamp or 0.0),
            }
        )
        rows.append(
            build_measurement(
                SPEC_BY_NAME[STACKING_FRAME_WEIGHT],
                1.0,
                context,
                subject,
                status=MeasurementStatus.ASSUMED,
                reason="Siril's filters may have dropped some frames",
            )
        )
    record_measurements(library.store(), rows)
    _nights(library)
    shares = _rows(library, STACKING_NIGHT_WEIGHT_SHARE)
    assert shares and all(
        row.status == MeasurementStatus.ASSUMED and "filters" in row.reason for row in shares
    )


def test_a_version_two_aperture_file_is_refused_by_the_subsets_and_left_alone(tmp_path: Path) -> None:
    """A subset never chooses apertures; an old file is left alone."""
    library = _Library(tmp_path, nights=3)
    apertures = Path(library.stack_path).parent / APERTURE_FILE_NAME
    body = json.dumps({"version": 2, "sets": {}})
    apertures.write_text(body, encoding="utf-8")
    result = _growth(library)
    assert all(subset.normalized_noise is None for subset in result.subsets)
    assert all("predate version 3" in subset.noise_reason for subset in result.subsets)
    assert apertures.read_text(encoding="utf-8") == body
    rows = _rows(library, STACKING_EMPTY_APERTURE_NOISE_NORMALIZED)
    assert rows and all("predate version 3" in row.reason for row in rows)


def test_sums_saved_in_other_apertures_are_not_paired_with_a_subset(tmp_path: Path) -> None:
    """The guard compares identities, not names."""
    from astrometricslib.pipelines.stacking.post_processing.difference_noise import (
        noise_sums_path,
        save_noise_sums,
    )

    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    outcome = measure_stack_noise(
        library.stack_path, None, str(Path(library.stack_path).parent / APERTURE_FILE_NAME)
    )
    save_noise_sums(library.stack_path, outcome.result)
    path = Path(noise_sums_path(library.stack_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["aperture_identity"] = "v3-someotherset"
    path.write_text(json.dumps(data), encoding="utf-8")
    _nights(library)
    rows = _rows(library, STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT)
    assert rows and all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("other apertures" in row.reason and "v3-someotherset" in row.reason for row in rows)


# -- The stack of the earlier nights (decision D11) ---------------------------


def _earlier_nights(library: _Library, **keywords: Any) -> Any:
    """Stack all nights but the last as a forecast subset, as the script does.

    Parameters
    ----------
    library : `_Library`
        The test library.
    **keywords
        Replacements for the call's keyword arguments.

    Returns
    -------
    result : `SubsetStackResult`
        The earlier-nights stack.
    """
    last_night = max(observing_night_id(frame.timestamp) for frame in library.target.frames)
    frames = [frame for frame in library.target.frames if observing_night_id(frame.timestamp) != last_night]
    arguments = {
        "context": library.context("earlier-run"),
        "configuration": library.configuration,
        "drivers": library.drivers,
        "stacking_slot": library.slot,
        "record_forecast_rows": True,
    }
    arguments.update(keywords)
    return stack_chosen_subset(
        library.target, frames, EARLIER_NIGHTS_VARIANT, library.stack_path, **arguments
    )


def _earlier_rows(library: _Library, metric: str) -> list[QualityMeasurement]:
    """Read the stored rows of one metric for the earlier-nights stack.

    Parameters
    ----------
    library : `_Library`
        The test library.
    metric : `str`
        The metric.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        Every row of the metric with the earlier-nights variant.
    """
    return library.store().query(
        metric=metric, target_id="M 99", variant=EARLIER_NIGHTS_VARIANT, latest_only=False
    )


def test_without_the_option_a_subset_stores_no_forecast_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the default, no weight, group or calibration row is stored."""
    library = _Library(tmp_path, nights=3)
    flat = tmp_path / "master_flat.fits"
    flat.write_bytes(b"flat")
    _grouped_stack(monkeypatch, library, flat)

    _earlier_nights(library, record_forecast_rows=False)

    assert _earlier_rows(library, STACKING_FRAME_WEIGHT) == []
    assert _earlier_rows(library, STACKING_GROUP_EXPOSURE_S) == []
    assert library.store().get_calibration_identities(["earlier-run"], "M 99") == []
    # The rows every subset stores are still there.
    assert len(_earlier_rows(library, STACKING_APERTURE_SET_NUMBER)) == 1
    assert len(_earlier_rows(library, STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT)) == 1


def test_the_nights_given_are_stacked_into_the_label_folder(
    tmp_path: Path,
) -> None:
    """Nights 0 and 1 are stacked into the label's folder."""
    library = _Library(tmp_path, nights=3)

    result = _earlier_nights(library)

    (stacked_paths,) = library.stacker.calls
    assert sorted(Path(path).name[:2] for path in stacked_paths) == ["n0"] * 5 + ["n1"] * 5
    assert result.label == EARLIER_NIGHTS_VARIANT
    assert result.frames_submitted == 10
    assert len(result.observing_night_ids) == 2
    expected = Path(library.stack_path).parent / "_subsets" / "earlier-nights" / "earlier-nights.fits"
    assert result.stack_path == str(expected)
    assert expected.is_file()


def test_the_earlier_nights_rows_carry_the_variant_and_the_library_stacks_apertures(tmp_path: Path) -> None:
    """The subset noise is in the library apertures, under its own run id."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})

    _earlier_nights(library)

    noise_rows = _earlier_rows(library, STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT)
    assert len(noise_rows) == 1
    (noise_row,) = noise_rows
    assert noise_row.subject.variant == EARLIER_NIGHTS_VARIANT
    assert noise_row.subject.subject_id == "M 99/_subsets/earlier-nights/earlier-nights.fits"
    assert noise_row.run_id == "earlier-run"
    library_apertures, _reason = load_apertures_with_reason(
        str(Path(library.stack_path).parent / APERTURE_FILE_NAME)
    )
    (set_row,) = _earlier_rows(library, STACKING_APERTURE_SET_NUMBER)
    assert set_row.value == aperture_identity_to_number(library_apertures.identity)
    assert set_row.subject.variant == EARLIER_NIGHTS_VARIANT
    (count_row,) = _earlier_rows(library, STACKING_EFFECTIVE_FRAME_COUNT)
    assert count_row.value == pytest.approx(10.0)
    # The library stack's own rows are untouched.
    assert [
        row.run_id for row in library.store().query(metric=STACKING_EFFECTIVE_FRAME_COUNT, library_only=True)
    ] == ["library-run"]


def _grouped_stack(
    monkeypatch: pytest.MonkeyPatch, library: _Library, calibration_file: Path
) -> list[list[float]]:
    """Make the subset stacker report two groups, one with a folded frame.

    The first group holds the first night's five frames, the last of them a
    180 s frame folded into a 60 s group (the group's registration reference
    is its first frame, 60 s, so that frame's scale is 1/3). The second group
    holds the second night's five frames of 120 s. The stand-in writes a real
    synthetic stack and reports the groups the way the stacker does.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the stacker, and the join of stored sky rates with profiles.
    library : `_Library`
        The test library; its frames get their exposure lengths here.
    calibration_file : `pathlib.Path`
        A file reported as the flat master the subset applied.

    Returns
    -------
    scales_seen : `list` [`list` [`float`]]
        Filled with the ``k_i`` list each prediction was given.
    """
    for frame in library.target.frames:
        night = int(Path(frame.path).name[1])
        frame.exposure = "120" if night == 1 else ("180" if frame.path.endswith("n0_04.fits") else "60")

    def stack(
        target_id: str, frames: list[FrameRecord], output_path: str, **_keywords: Any
    ) -> SubsetStackOutcome:
        """Write a stack and report two exposure groups.

        Parameters
        ----------
        target_id : `str`
            The target (unused).
        frames : `list` [`FrameRecord`]
            The subset's frames, first night then second.
        output_path : `str`
            Where to write the stack.

        Returns
        -------
        outcome : `SubsetStackOutcome`
            The stack and its diagnostics.
        """
        _write_stack(output_path, 6.0 / math.sqrt(len(frames)), seed=5)
        groups = [frames[:5], frames[5:]]
        exposures = [60.0, 120.0]
        folder = Path(output_path).parent
        entries, summaries = [], []
        for index, (group, exposure) in enumerate(zip(groups, exposures, strict=True)):
            paths = [frame.path for frame in group]
            entries.append({
                "paths": paths,
                "frame_paths": paths,
                "registration_frames": [
                    {
                        "dx": 0.0 if position == 0 else 1.5,
                        "dy": 0.0 if position == 0 else -0.5,
                        "star_count": 50,
                    }
                    for position in range(len(paths))
                ],
                "registration_included": [True] * len(paths),
                "weight": len(paths) * exposure**2 / 100.0,
                "frame_count": len(paths),
                "exposure_seconds": exposure,
                "frame_noise_counts": 10.0,
                "gain": 1.0,
                "gain_measured": True,
                "sky_level_per_second": 0.01,
                "stack_weight": None,
                "holds_folded_frames": index == 0,
                "summary_index": index,
            })
            summaries.append({
                "exposure_seconds": exposure,
                "frames_submitted": len(paths),
                "frames_stacked": len(paths),
                "dark_applied": True,
                "stack_path": str(folder / "groups" / f"earlier-nights_exp{exposure:g}s.fits"),
                "gain_applied": None if index == 0 else 1.02,
            })
        diagnostics = {
            "combined_frame_paths": [frame.path for frame in frames],
            "symlinked_light_paths": [frame.path for frame in frames],
            "registration_included": [True] * len(frames),
            "images_stacked": len(frames),
            "exposure_group_weights": entries,
            "exposure_group_summaries": summaries,
            "flat_frame_paths": [str(calibration_file)],
        }
        return SubsetStackOutcome(output_path, diagnostics, None, (0, 0), None, False)

    scales_seen: list[list[float]] = []

    def inputs(
        members: list[Any],
        weights: Any,
        target_id: str,
        configuration: Any,
        store: Any,
        weights_reason: str = "",
        exposure_scales: Any = None,
    ) -> list[FrameNoiseInput]:
        """Give each frame a fixed sky and keep the scales it was asked with.

        Returns
        -------
        inputs : `list` [`FrameNoiseInput`]
            One entry for each member, carrying its scale.
        """
        scales = list(exposure_scales)
        scales_seen.append(scales)
        return [
            FrameNoiseInput(weight, 100.0, 3.8, 0.783, exposure_scale=scale)
            for weight, scale in zip(weights, scales, strict=True)
        ]

    monkeypatch.setattr(subset_noise_run, "stack_frame_subset", stack)
    monkeypatch.setattr(record_science, "frame_noise_inputs_from_store", inputs)
    return scales_seen


def test_the_earlier_nights_store_weights_group_rows_prediction_and_calibration_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything the forecast check reads is stored for the subset."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    flat = tmp_path / "master_flat.fits"
    flat.write_bytes(b"flat")
    scales_seen = _grouped_stack(monkeypatch, library, flat)

    result = _earlier_nights(library)

    assert result.stack_error == ""
    # One weight for each of the ten frames, on its own subject, mean 1.
    weights = _earlier_rows(library, STACKING_FRAME_WEIGHT)
    assert len(weights) == 10
    assert all(row.subject.level == SubjectLevel.FRAME for row in weights)
    assert all(row.subject.variant == EARLIER_NIGHTS_VARIANT for row in weights)
    assert all(row.subject.subject_id.endswith(f"#{EARLIER_NIGHTS_VARIANT}") for row in weights)
    assert sum(row.value for row in weights) == pytest.approx(10.0)
    # Group weights are N t^2 / s^2 = 36 and 144, shared evenly inside a group.
    by_night = {row.subject.observing_night_id: row.value for row in weights}
    assert len(by_night) == 2
    assert max(by_night.values()) / min(by_night.values()) == pytest.approx(4.0)
    # The library stack's own weights are still the newest rows of its frames.
    library_weights = library.store().query(metric=STACKING_FRAME_WEIGHT, library_only=True)
    assert len(library_weights) == 15
    assert {row.run_id for row in library_weights} == {"library-run"}
    # The group rows are on the subset's own group subjects.
    exposures = _earlier_rows(library, STACKING_GROUP_EXPOSURE_S)
    assert sorted((row.subject.subject_id, row.value) for row in exposures) == [
        ("M 99/_subsets/earlier-nights/groups/earlier-nights_exp120s.fits", 120.0),
        ("M 99/_subsets/earlier-nights/groups/earlier-nights_exp60s.fits", 60.0),
    ]
    assert all(row.subject.variant == EARLIER_NIGHTS_VARIANT for row in exposures)
    assert sorted(row.value for row in _earlier_rows(library, STACKING_GROUP_FRAMES_STACKED)) == [5.0, 5.0]
    applied = {
        row.subject.subject_id.split("_exp")[1]: row.value
        for row in _earlier_rows(library, STACKING_GROUP_GAIN_APPLIED)
    }
    assert applied["120s.fits"] == pytest.approx(1.02)
    references = {
        row.subject.subject_id.split("_exp")[1]: row.value
        for row in _earlier_rows(library, STACKING_GROUP_REFERENCE_EXPOSURE_S)
    }
    assert references == {"60s.fits": 60.0, "120s.fits": 120.0}
    # The predicted noise and the scale are on the subset's stack.
    (predicted,) = _earlier_rows(library, STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT)
    assert predicted.subject.subject_id == "M 99/_subsets/earlier-nights/earlier-nights.fits"
    assert predicted.value is not None
    assert len(_earlier_rows(library, STACKING_NORMALIZATION_SCALE)) == 1
    # The calibration record is filed under the subset's subject and its run.
    (record,) = library.store().get_calibration_identities(["earlier-run"], "M 99")
    assert record.subject_id == "M 99/_subsets/earlier-nights/earlier-nights.fits"
    assert [master.path for master in record.flat_masters] == [str(flat)]
    assert scales_seen


def test_the_folded_frames_scale_reaches_the_earlier_nights_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 180 s frame folded into a 60 s group has k = 1/3 in the prediction."""
    library = _Library(tmp_path, nights=3)
    library.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    flat = tmp_path / "master_flat.fits"
    flat.write_bytes(b"flat")
    scales_seen = _grouped_stack(monkeypatch, library, flat)

    _earlier_nights(library)

    assert scales_seen[0] == pytest.approx([1.0, 1.0, 1.0, 1.0, 60.0 / 180.0] + [1.0] * 5)
    # A prediction made with k = 1 differs: the scale changes the stored noise.
    (predicted,) = _earlier_rows(library, STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT)
    library_two = _Library(tmp_path / "again", nights=3)
    library_two.record_library_weights({0: 1.0, 1: 1.0, 2: 1.0})
    scales_unscaled = _grouped_stack(monkeypatch, library_two, flat)
    monkeypatch.setattr(
        record_science,
        "frame_noise_inputs_from_store",
        lambda members, weights, *_a, **_k: [
            FrameNoiseInput(weight, 100.0, 3.8, 0.783) for weight in weights
        ],
    )
    _earlier_nights(library_two)
    (unscaled,) = library_two.store().query(
        metric=STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT, variant=EARLIER_NIGHTS_VARIANT
    )
    assert predicted.value != pytest.approx(unscaled.value)
    assert scales_unscaled == []


def test_the_odd_even_difference_model_ratio_uses_the_halves_exposure_scales() -> None:
    """A frame with k = 2 has four times the variance in the modeled ratio."""
    from astrometricslib.pipelines.stacking.post_processing.expected_noise import FrameNoiseInput as Input

    members = [
        FrameRecord(path=f"/frames/lights/M 99/Scope/Cam/f{index}.fits", camera="Cam") for index in range(4)
    ]
    library = subset_noise_run.LibraryRun(
        members=members,
        weight_by_path={member.path: 2.0 if index == 0 else 1.0 for index, member in enumerate(members)},
    )
    seen: list[Any] = []

    def inputs(
        members_given: list[Any], weights: Any, *_arguments: Any, exposure_scales: Any = None
    ) -> list[Input]:
        """Keep the scales and give every frame the same sky.

        Returns
        -------
        inputs : `list` [`FrameNoiseInput`]
            One entry for each member.
        """
        seen.append(list(exposure_scales))
        return [Input(1.0, 100.0, 3.8, 0.783, exposure_scale=scale) for scale in exposure_scales]

    odd = {members[0].path: 0.5, members[2].path: 0.5}
    even = {members[1].path: 0.5, members[3].path: 0.5}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subset_noise_run, "frame_noise_inputs_from_store", inputs)
        plain, _ = subset_noise_run._model_ratio_for_library(library, "M 99", None, odd, even)
        scaled, _ = subset_noise_run._model_ratio_for_library(
            library, "M 99", None, odd, even, {members[0].path: 2.0}
        )
    assert seen == [[1.0] * 4, [2.0, 1.0, 1.0, 1.0]]
    variance = (100.0 + 3.8**2) / 0.783**2
    library_shares = {member.path: (2.0 if index == 0 else 1.0) / 5.0 for index, member in enumerate(members)}
    expected = model_variance_ratio(
        odd,
        even,
        library_shares,
        {member.path: variance * (4.0 if index == 0 else 1.0) for index, member in enumerate(members)},
    )
    assert plain != pytest.approx(scaled)
    assert scaled == pytest.approx(expected)
