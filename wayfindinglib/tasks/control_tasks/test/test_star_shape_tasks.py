"""Purpose: Test that the star-shape review keeps slow reads until data change.

Description: `get_star_shape_review` is asked for on every visit to the
Image Processing screen. These tests use a stand-in context and count the
slow reads: the target's stored rows, the performance envelope behind the
frame roundness limit, and the stack baseline. Each is read once while its
data version stays the same, read again when the version changes, and read
every time when the version cannot be worked out. The set-aside listing is
read once per review. The target's rows are kept with the science
library's opaque data version of the target's quality rows. Further tests
check what the version of the roundness limit follows: live guiding samples
and frames of nights the limit does not use leave it as it is. A randomized
test checks that the kept roundness signature gives equal versions exactly
when fresh signatures are equal, and that an unchanged library reads no
measurements.
"""

import dataclasses
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from astrometricslib.arguments import observing_night_id
from astrometricslib.drivers import VersionedCache
from astrometricslib.exceptions import StorageError
from wayfindinglib.analytics.capture_history import (
    STAR_ELONGATION_METRIC,
    STAR_WIDTH_METRIC,
    roundness_baseline_signature,
)
from wayfindinglib.analytics.night_index import NightFrame, NightIndex
from wayfindinglib.analytics.star_shape_review import StoredShapeRows
from wayfindinglib.models.session.star_shape_review import FrameStarShape
from wayfindinglib.tasks.control_tasks import star_shape_tasks

TARGET_ID = "Target"
"""The id of the stand-in target."""

FRAME_TIMESTAMP = 1.0e9
"""When the stand-in target's one light frame was taken."""

ROUNDNESS_LIMIT = 0.9
"""The made-up roundness limit the stand-in envelope gives."""

STACK_LIMIT = 0.95
"""The made-up stack baseline the stand-in reader gives."""


class _Counts:
    """Counts the slow reads and holds the data versions the test sets."""

    def __init__(self) -> None:
        """Start with no reads and fixed versions."""
        self.rows = 0
        self.envelopes = 0
        self.stack_limits = 0
        self.set_aside = 0
        self.target_version: Any = ("target", 1)
        self.review_version: Any = ("review", 1)
        self.stack_version: Any = ("stack", 1)


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch) -> _Counts:
    """Replace the slow reads and the version functions with counting ones.

    Returns
    -------
    counts : `_Counts`
        The counters and versions.
    """
    counts = _Counts()

    def read_rows(_astrometrics: Any, _target_id: str, **_options: Any) -> StoredShapeRows:
        """Count a read and give one frame with an elongation and a stack.

        Returns
        -------
        stored : `StoredShapeRows`
            The made-up numbers.
        """
        counts.rows += 1
        return StoredShapeRows(
            frames={f"{TARGET_ID}/a.fits": {"frame.star_elongation": (1.2, 7)}},
            stack=FrameStarShape(path=f"{TARGET_ID}/stack.fits", elongation=1.05),
        )

    def envelope(*_arguments: Any) -> Any:
        """Count an envelope build and give one with the made-up limit.

        Returns
        -------
        envelope : `types.SimpleNamespace`
            Has ``value``.
        """
        counts.envelopes += 1
        return SimpleNamespace(value=lambda _name: ROUNDNESS_LIMIT)

    def stack_limit(_astrometrics: Any, _subject: str) -> float:
        """Count a read of the stack baseline.

        Returns
        -------
        limit : `float`
            The made-up baseline.
        """
        counts.stack_limits += 1
        return STACK_LIMIT

    monkeypatch.setattr(star_shape_tasks, "read_stored_shape_rows", read_rows)
    monkeypatch.setattr(star_shape_tasks.night_analysis, "recorded_sessions", lambda _context: ([], []))
    monkeypatch.setattr(star_shape_tasks.night_analysis, "performance_envelope", envelope)
    monkeypatch.setattr(star_shape_tasks, "read_stack_roundness_low_limit", stack_limit)
    monkeypatch.setattr(
        star_shape_tasks, "roundness_limit_data_version", lambda _context, _night: counts.review_version
    )
    monkeypatch.setattr(star_shape_tasks, "library_data_version", lambda _read, _what: counts.stack_version)
    return counts


def _context(counts: _Counts) -> SimpleNamespace:
    """Build a stand-in control context with one target frame.

    Returns
    -------
    context : `types.SimpleNamespace`
        Has ``astrometrics``, ``config`` and ``star_shape_cache``.
    """

    def get_set_aside(_target: Any) -> list[Any]:
        """Count a read of the set-aside listing and give none.

        Returns
        -------
        frames : `list`
            Empty.
        """
        counts.set_aside += 1
        return []

    def get_data_version(**_filters: Any) -> str:
        """Give the target's made-up data version, or fail without one.

        Returns
        -------
        version : `str`
            The version the test set.

        Raises
        ------
        StorageError
            When the test set no version, as an unreadable library does.
        """
        if counts.target_version is None:
            raise StorageError("the quality store is locked")
        return str(counts.target_version)

    astrometrics = SimpleNamespace(
        targets=SimpleNamespace(frames=SimpleNamespace(get_set_aside=get_set_aside)),
        quality=SimpleNamespace(records=SimpleNamespace(get_data_version=get_data_version)),
    )
    return SimpleNamespace(astrometrics=astrometrics, config=object(), star_shape_cache=VersionedCache())


def _target() -> SimpleNamespace:
    """Build the stand-in target with one light frame.

    Returns
    -------
    target : `types.SimpleNamespace`
        Has ``id``, ``frames`` and ``frame_subject_id``.
    """
    return SimpleNamespace(
        id=TARGET_ID,
        frames=[SimpleNamespace(path="a.fits", role="LIGHT", timestamp=FRAME_TIMESTAMP)],
        frame_subject_id=lambda frame: f"{TARGET_ID}/{frame.path}",
    )


def test_a_second_review_reuses_the_rows_and_limits(counts: _Counts) -> None:
    """With no data change, the slow reads happen once for two reviews."""
    context = _context(counts)

    first = star_shape_tasks.get_star_shape_review(context, _target())
    second = star_shape_tasks.get_star_shape_review(context, _target())

    assert first == second
    assert first.roundness.numbers["roundness_low_limit"] == STACK_LIMIT
    assert (counts.rows, counts.envelopes, counts.stack_limits) == (1, 1, 1)
    assert counts.set_aside == 2


def test_each_part_is_read_again_only_when_its_own_data_change(counts: _Counts) -> None:
    """A change of one version reads only the part that depends on it."""
    context = _context(counts)
    star_shape_tasks.get_star_shape_review(context, _target())

    counts.target_version = ("target", 2)
    star_shape_tasks.get_star_shape_review(context, _target())
    assert (counts.rows, counts.envelopes, counts.stack_limits) == (2, 1, 1)

    counts.review_version = ("review", 2)
    star_shape_tasks.get_star_shape_review(context, _target())
    assert (counts.rows, counts.envelopes, counts.stack_limits) == (2, 2, 1)

    counts.stack_version = ("stack", 2)
    star_shape_tasks.get_star_shape_review(context, _target())
    assert (counts.rows, counts.envelopes, counts.stack_limits) == (2, 2, 2)


def test_a_night_has_its_own_roundness_limit(counts: _Counts) -> None:
    """The limit for one night is not the limit for all nights."""
    context = _context(counts)

    star_shape_tasks.get_star_shape_review(context, _target())
    star_shape_tasks.get_star_shape_review(context, _target(), night=observing_night_id(FRAME_TIMESTAMP))

    assert counts.envelopes == 2
    assert counts.rows == 1


def test_without_a_version_nothing_is_kept(counts: _Counts) -> None:
    """When the versions cannot be worked out, every review reads again."""
    counts.target_version = counts.review_version = counts.stack_version = None
    context = _context(counts)

    star_shape_tasks.get_star_shape_review(context, _target())
    star_shape_tasks.get_star_shape_review(context, _target())

    assert (counts.rows, counts.envelopes, counts.stack_limits) == (2, 2, 2)


MEASURED_FRAMES = 10
"""How many measured frames each stand-in night has: the minimum for a night
to join the roundness baseline (``MINIMUM_FRAMES_PER_NIGHT``)."""

OLD_NIGHT, TONIGHT = "2026-11-20", "2026-11-26"
"""The stand-in finished night and the stand-in night in progress."""


def _subject(night: str, number: int) -> str:
    """Name the stand-in frame `number` of a night.

    Returns
    -------
    subject : `str`
        The frame's subject id.
    """
    return f"{TARGET_ID}/{night}_{number:02d}.fits"


class _VersionInputs:
    """The stand-in inputs of `roundness_limit_data_version`."""

    def __init__(self) -> None:
        """Start with ten measured frames on an earlier night and tonight."""
        self.records: Any = ("records", 1)
        self.frames = [
            NightFrame(night=night, target_id=TARGET_ID, subject_id=_subject(night, number), exposure_s=60.0)
            for night in (OLD_NIGHT, TONIGHT)
            for number in range(MEASURED_FRAMES)
        ]
        self.values: dict[str, dict[str, tuple[float, int]]] = {
            frame.subject_id: {STAR_WIDTH_METRIC: (2.5, 1), STAR_ELONGATION_METRIC: (1.1, 2)}
            for frame in self.frames
        }
        self.measurement_reads = 0
        self.context = SimpleNamespace(config=object(), butler=object(), star_shape_cache=VersionedCache())

    def measurement_version(self) -> tuple[str, ...]:
        """Give the stand-in data version of the stored measurements.

        It is made from the stored values themselves, so it changes exactly
        when they do, as the real version changes with every added or
        removed row.

        Returns
        -------
        version : `tuple` [`str`, ...]
            One text holding every stored value.
        """
        return (
            repr(sorted((subject, tuple(sorted(values.items()))) for subject, values in self.values.items())),
        )

    def version(self, night: str | None) -> Any:
        """Work out the limit version with the stand-in context.

        The context, and so its `star_shape_cache`, is the same for every
        call, as in the running app.

        Returns
        -------
        version : `Any`
            See `roundness_limit_data_version`.
        """
        return star_shape_tasks.roundness_limit_data_version(self.context, night)

    def add_frame(self, night: str, number: int, **fields: Any) -> NightFrame:
        """Index one more frame of a night, not yet measured.

        Returns
        -------
        frame : `NightFrame`
            The new frame.
        """
        frame = NightFrame(
            night=night,
            target_id=TARGET_ID,
            subject_id=_subject(night, number),
            **{"exposure_s": 60.0, **fields},
        )
        self.frames.append(frame)
        return frame

    def measure(self, subject_id: str, width: float = 2.5, elongation: float = 1.2) -> None:
        """Store a width and an elongation row for a frame."""
        self.values[subject_id] = {STAR_WIDTH_METRIC: (width, 3), STAR_ELONGATION_METRIC: (elongation, 4)}

    def replace_frame(self, subject_id: str, **fields: Any) -> None:
        """Change fields of an indexed frame, as a document edit does."""
        self.frames = [
            dataclasses.replace(frame, **fields) if frame.subject_id == subject_id else frame
            for frame in self.frames
        ]


@pytest.fixture
def version_inputs(monkeypatch: pytest.MonkeyPatch) -> _VersionInputs:
    """Replace the reads behind `roundness_limit_data_version`.

    Returns
    -------
    inputs : `_VersionInputs`
        The records version, the indexed frames and their stored values,
        which the test changes.
    """
    inputs = _VersionInputs()

    def night_index(_context: Any, tonight: str) -> NightIndex:
        """Build the index of the stand-in frames.

        Returns
        -------
        index : `NightIndex`
            The frames, with `tonight` as the night in progress.
        """
        frames = sorted(inputs.frames, key=lambda frame: (frame.night, frame.target_id, frame.subject_id))
        return NightIndex(
            tonight=tonight, frames=tuple(frames), ekos_nights=frozenset(), guiding_nights=frozenset()
        )

    def kept_measurements(_context: Any, _version: Any = None) -> dict[str, Any]:
        """Give the stand-in stored values of the frames.

        Returns
        -------
        measurements : `dict`
            A copy of the values, so later changes do not reach it.
        """
        inputs.measurement_reads += 1
        return {subject: dict(values) for subject, values in inputs.values.items()}

    monkeypatch.setattr(star_shape_tasks, "report_context_version", lambda *_: inputs.records)
    monkeypatch.setattr(star_shape_tasks, "build_night_index", night_index)
    monkeypatch.setattr(star_shape_tasks.night_analysis, "kept_frame_measurements", kept_measurements)
    monkeypatch.setattr(
        star_shape_tasks.night_analysis,
        "frame_measurement_version",
        lambda _context: inputs.measurement_version(),
    )
    return inputs


def test_an_indexed_frame_that_is_not_measured_leaves_the_version(version_inputs: _VersionInputs) -> None:
    """A new frame changes nothing until it has width and elongation rows."""
    before = version_inputs.version(None)
    frame = version_inputs.add_frame(TONIGHT, 50)
    assert version_inputs.version(None) == before

    # A width row alone still cannot make the frame count for roundness.
    version_inputs.values[frame.subject_id] = {STAR_WIDTH_METRIC: (2.5, 5)}
    assert version_inputs.version(None) == before

    version_inputs.measure(frame.subject_id)
    assert version_inputs.version(None) != before


def test_a_measured_frames_exposure_spectral_flag_and_night_count(version_inputs: _VersionInputs) -> None:
    """Editing a measured frame changes the version; an unmeasured one not."""
    measured = _subject(OLD_NIGHT, 0)
    unmeasured = version_inputs.add_frame(OLD_NIGHT, 50).subject_id
    before = version_inputs.version(None)

    version_inputs.replace_frame(unmeasured, exposure_s=5.0, is_spectral=True)
    assert version_inputs.version(None) == before

    version_inputs.replace_frame(measured, exposure_s=5.0)
    after_exposure = version_inputs.version(None)
    assert after_exposure != before

    version_inputs.replace_frame(measured, is_spectral=True)
    after_spectral = version_inputs.version(None)
    assert after_spectral != after_exposure

    version_inputs.replace_frame(measured, is_spectral=False, exposure_s=60.0, night=TONIGHT)
    assert version_inputs.version(None) not in (before, after_exposure, after_spectral)


def test_a_removed_or_set_aside_frame_changes_the_version(version_inputs: _VersionInputs) -> None:
    """A measured frame that is deleted or set aside changes the version."""
    before = version_inputs.version(None)
    version_inputs.frames = [
        frame for frame in version_inputs.frames if frame.subject_id != _subject(OLD_NIGHT, 3)
    ]

    assert version_inputs.version(None) != before


def test_a_night_with_too_few_measured_frames_leaves_the_version(version_inputs: _VersionInputs) -> None:
    """A night counts only once it has the minimum of measured frames."""
    before = version_inputs.version(None)
    for number in range(MEASURED_FRAMES - 1):
        frame = version_inputs.add_frame("2026-11-27", number)
        version_inputs.measure(frame.subject_id)
        assert version_inputs.version(None) == before

    frame = version_inputs.add_frame("2026-11-27", MEASURED_FRAMES)
    version_inputs.measure(frame.subject_id)
    assert version_inputs.version(None) != before


def test_a_new_elongation_of_a_measured_frame_changes_the_version(version_inputs: _VersionInputs) -> None:
    """A frame measured again with a new elongation changes the version."""
    before = version_inputs.version(None)
    version_inputs.measure(_subject(OLD_NIGHT, 1), elongation=1.6)

    assert version_inputs.version(None) != before


def test_the_limit_version_ignores_what_the_limit_does_not_read(version_inputs: _VersionInputs) -> None:
    """For one night, frames of that night and later leave the version alone.

    The live guiding samples are inside the records version, which counts
    them in coarse steps (`report_context_version`), so they are not
    watched here.
    """
    before = version_inputs.version(TONIGHT)

    version_inputs.measure(_subject(TONIGHT, 0), elongation=1.7)
    version_inputs.measure(version_inputs.add_frame(TONIGHT, 50).subject_id)

    assert version_inputs.version(TONIGHT) == before


def test_the_limit_version_follows_what_the_limit_reads(version_inputs: _VersionInputs) -> None:
    """Records and earlier frames change the version.

    Without a night every frame counts, so tonight's frame changes it too.
    """
    night_before = version_inputs.version(TONIGHT)
    all_before = version_inputs.version(None)

    version_inputs.measure(_subject(TONIGHT, 0), elongation=1.7)
    assert version_inputs.version(TONIGHT) == night_before
    assert version_inputs.version(None) != all_before

    version_inputs.measure(_subject(OLD_NIGHT, 0), elongation=1.7)
    assert version_inputs.version(TONIGHT) != night_before
    night_before = version_inputs.version(TONIGHT)

    version_inputs.records = ("records", 2)
    assert version_inputs.version(TONIGHT) != night_before


def test_the_limit_version_is_unknown_when_the_records_are(version_inputs: _VersionInputs) -> None:
    """A records version that cannot be read means the limit is not kept."""
    version_inputs.records = None

    assert version_inputs.version(None) is None


def test_the_limit_version_is_unknown_when_the_measurements_cannot_be_read(
    version_inputs: _VersionInputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreadable measurements mean the limit is not kept."""

    def fail(_context: Any, _version: Any = None) -> None:
        """Fail as an unreadable quality store does.

        Raises
        ------
        StorageError
            Always.
        """
        raise StorageError("the quality store is locked")

    monkeypatch.setattr(star_shape_tasks.night_analysis, "kept_frame_measurements", fail)

    assert version_inputs.version(None) is None


def test_indexing_a_frame_does_not_rebuild_the_limit_but_measuring_it_does(
    version_inputs: _VersionInputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new frame rebuilds the limit when its rows land, not when indexed."""
    builds: list[int] = []

    def envelope(*_arguments: Any) -> Any:
        """Count an envelope build.

        Returns
        -------
        envelope : `types.SimpleNamespace`
            Has ``value``.
        """
        builds.append(1)
        return SimpleNamespace(value=lambda _name: ROUNDNESS_LIMIT)

    monkeypatch.setattr(star_shape_tasks.night_analysis, "recorded_sessions", lambda _context: ([], []))
    monkeypatch.setattr(star_shape_tasks.night_analysis, "performance_envelope", envelope)
    context = SimpleNamespace(config=object(), butler=object(), star_shape_cache=VersionedCache())

    assert star_shape_tasks.kept_roundness_limit(context, None) == ROUNDNESS_LIMIT
    frame = version_inputs.add_frame(TONIGHT, 50)
    star_shape_tasks.kept_roundness_limit(context, None)
    assert len(builds) == 1

    version_inputs.measure(frame.subject_id)
    star_shape_tasks.kept_roundness_limit(context, None)
    star_shape_tasks.kept_roundness_limit(context, None)
    assert len(builds) == 2


def _fresh_signature(inputs: _VersionInputs, night: str | None) -> Any:
    """Work out the roundness signature from scratch, with no cache.

    Returns
    -------
    signature : `tuple`
        `roundness_baseline_signature` of the frames the limit for `night`
        uses, as the version worked it out before it kept the signature.
    """
    frames = [frame for frame in inputs.frames if night is None or frame.night < night]
    return roundness_baseline_signature(frames, inputs.values, night)


def _pick(generator: np.random.Generator, choices: list[Any]) -> Any:
    """Pick one item of a list.

    Returns
    -------
    choice : `Any`
        One of `choices`.
    """
    return choices[int(generator.integers(len(choices)))]


def _random_change(inputs: _VersionInputs, generator: np.random.Generator, step: int) -> str:
    """Make one random change to the frames or their stored values.

    Returns
    -------
    change : `str`
        The kind of change made. ``"nothing"`` changes nothing.
    """
    change = _pick(
        generator,
        [
            "nothing",
            "nothing",
            "index",
            "width_only",
            "measure",
            "measure",
            "exposure",
            "spectral",
            "night",
            "remove",
            "copy",
        ],
    )
    nights = [OLD_NIGHT, "2026-11-23", TONIGHT, "2026-11-27"]
    if change == "index" or not inputs.frames:
        inputs.add_frame(_pick(generator, nights), 100 + step)
        return "index"
    frame = _pick(generator, inputs.frames)
    if change == "width_only":
        inputs.values[frame.subject_id] = {STAR_WIDTH_METRIC: (2.5, step)}
    elif change == "measure":
        inputs.measure(
            frame.subject_id,
            width=_pick(generator, [-1.0, 2.0, 2.5, 3.0]),
            elongation=_pick(generator, [0.9, 1.0, 1.1, 1.3, 1.6]),
        )
    elif change == "exposure":
        inputs.replace_frame(frame.subject_id, exposure_s=_pick(generator, [5.0, 30.0, 60.0]))
    elif change == "spectral":
        inputs.replace_frame(frame.subject_id, is_spectral=not frame.is_spectral)
    elif change == "night":
        inputs.replace_frame(frame.subject_id, night=_pick(generator, nights))
    elif change == "remove":
        inputs.frames = [held for held in inputs.frames if held is not frame]
    elif change == "copy":
        # The same frame read again from its document: equal, but a new object.
        inputs.replace_frame(frame.subject_id)
    return change


@pytest.mark.parametrize("night", [None, TONIGHT])
def test_the_kept_signature_changes_exactly_when_a_fresh_one_does(
    version_inputs: _VersionInputs, night: str | None
) -> None:
    """Two versions are equal exactly when fresh signatures would be equal.

    Random changes are made one at a time: frames indexed, measured (with
    usable or unusable rows), edited, moved, removed or read again, and
    steps with no change. After each one, the version (which keeps the
    signature in the cache) is compared with every earlier version, and
    fresh signatures of the same states are compared the same way. The two
    comparisons must agree every time. A step that changes nothing must
    not read the stored measurements.
    """
    generator = np.random.default_rng(20261127)
    # Nights a little above and just at the minimum, so single changes
    # move them across it in both directions.
    for number in range(MEASURED_FRAMES, MEASURED_FRAMES + 2):
        version_inputs.measure(version_inputs.add_frame(OLD_NIGHT, number).subject_id)
    for number in range(MEASURED_FRAMES):
        version_inputs.measure(version_inputs.add_frame("2026-11-23", number).subject_id)
    history: list[tuple[Any, Any]] = []
    equal_pairs = 0
    for step in range(250):
        reads = version_inputs.measurement_reads
        change = _random_change(version_inputs, generator, step)
        version = version_inputs.version(night)
        if change == "nothing" and history:
            assert version_inputs.measurement_reads == reads
        fresh = _fresh_signature(version_inputs, night)
        for earlier_version, earlier_fresh in history:
            assert (version == earlier_version) == (fresh == earlier_fresh)
            equal_pairs += fresh == earlier_fresh
        history.append((version, fresh))
    # Both kinds of pair happened, so the check above was not empty.
    assert 0 < equal_pairs < len(history) * (len(history) - 1) // 2


def test_an_unchanged_library_reads_no_measurements_for_the_version(version_inputs: _VersionInputs) -> None:
    """While the frames and the measurements' version stay, nothing is read.

    This holds even when the kept measurements were dropped elsewhere,
    because the signature itself is kept.
    """
    first = version_inputs.version(None)
    reads = version_inputs.measurement_reads

    assert version_inputs.version(None) == first
    assert version_inputs.measurement_reads == reads

    version_inputs.measure(_subject(OLD_NIGHT, 2), elongation=1.4)
    assert version_inputs.version(None) != first
    assert version_inputs.measurement_reads == reads + 1


def test_without_a_measurement_version_the_signature_is_not_kept(
    version_inputs: _VersionInputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable data version still gives a version, worked out fresh."""
    monkeypatch.setattr(star_shape_tasks.night_analysis, "frame_measurement_version", lambda _context: None)

    first = version_inputs.version(None)
    second = version_inputs.version(None)

    assert first == second == (version_inputs.records, None, _fresh_signature(version_inputs, None))
    assert version_inputs.measurement_reads == 2
