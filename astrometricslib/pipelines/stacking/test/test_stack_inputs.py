"""Tests for deciding whether a stack on disk is still up to date.

A stack is skipped only when its frames, the calibration frames chosen for
them, the settings that change its pixels and the stacking code are all the
same as when it was made. These tests change one input at a time and check
that the fingerprint moves, that the reasons given for a rebuild are right,
and that anything uncertain leads to a rebuild and never to a wrong skip. A
last test scans the stacking code for every setting it reads, so a new setting
cannot be forgotten.
"""

import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.pipelines.stacking.pre_processing import stack_inputs as si

DEFAULT_SETTINGS: dict[str, Any] = {
    "get_stack_weight": None,
    "get_stack_rejection_sigma_mode": "adaptive",
    "get_stack_rejection_sigma": (3.0, 3.0),
    "get_stack_generate_rejmap": True,
    "get_exposure_group_gain_tolerance": 0.05,
    "get_stack_filter_wfwhm_percentile": None,
    "get_stack_filter_round_percentile": None,
    "get_trim_noisy_stack_edges_enabled": True,
    "get_quarantine_bad_frames_enabled": True,
    "get_background_homogeneity_check_enabled": True,
}


class FakeConfiguration:
    """A configuration whose stack settings are plain values.

    Each name in `DEFAULT_SETTINGS` is a method returning its value, so a test
    can override one at a time.
    """

    def __init__(self, **overrides: Any) -> None:
        """Set the values, taking `overrides` over the defaults."""
        self.values = {**DEFAULT_SETTINGS, **overrides}

    def __getattr__(self, name: str) -> Any:
        """Give the getter for a setting.

        Returns
        -------
        getter : `Callable`
            A function returning the setting's value.

        Raises
        ------
        AttributeError
            If `name` is not one of the settings.
        """
        if name in self.values:
            return lambda: self.values[name]
        raise AttributeError(name)


class FakeLibrary:
    """A calibration library that returns fixed lists of files."""

    def __init__(self, darks: list[str], biases: list[str], flats: list[str]) -> None:
        """Store the files each lookup returns."""
        self.darks, self.biases, self.flats = darks, biases, flats

    def get_dark_frames(self, **_: Any) -> list[str]:
        """Return the dark frames.

        Returns
        -------
        paths : `list` [`str`]
            The stored dark file paths.
        """
        return list(self.darks)

    def get_bias_frames(self, **_: Any) -> list[str]:
        """Return the bias frames.

        Returns
        -------
        paths : `list` [`str`]
            The stored bias file paths.
        """
        return list(self.biases)

    def get_flat_frames(self, **_: Any) -> list[str]:
        """Return the flat frames.

        Returns
        -------
        paths : `list` [`str`]
            The stored flat file paths.
        """
        return list(self.flats)


def make_files(folder: Path, names: list[str]) -> list[str]:
    """Write small files and give their paths.

    Returns
    -------
    paths : `list` [`str`]
        The paths, in the order of `names`.
    """
    paths = []
    for name in names:
        path = folder / name
        path.write_bytes(b"data")
        paths.append(str(path))
    return paths


def make_frame(path: str) -> SimpleNamespace:
    """Build a frame record that only carries what the record reads.

    Returns
    -------
    frame : `SimpleNamespace`
        A frame at `path` taken with one camera, gain, offset and exposure.
    """
    return SimpleNamespace(
        path=path, telescope="Apertura 75Q", camera="ZWO", iso="0", offset="10", filter="L", exposure="60.0"
    )


@pytest.fixture
def inputs(tmp_path: Path) -> SimpleNamespace:
    """Make frames, calibration files and a library on disk.

    Returns
    -------
    inputs : `SimpleNamespace`
        ``frames``, ``darks``, ``biases``, ``flats`` (paths) and ``library``.
    """
    frames = make_files(tmp_path, [f"light_{index}.fits" for index in range(5)])
    darks = make_files(tmp_path, ["dark_1.fits", "dark_2.fits"])
    biases = make_files(tmp_path, ["bias_1.fits"])
    flats = make_files(tmp_path, ["flat_1.fits", "flat_2.fits"])
    return SimpleNamespace(
        frames=frames, darks=darks, biases=biases, flats=flats, library=FakeLibrary(darks, biases, flats)
    )


def record_for(
    inputs: SimpleNamespace,
    frames: list[str] | None = None,
    configuration: FakeConfiguration | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the record of the fixture's inputs, with optional changes.

    Returns
    -------
    record : `dict`
        The record.
    """
    record = si.build_stack_inputs_record(
        [make_frame(path) for path in (frames if frames is not None else inputs.frames)],
        configuration or FakeConfiguration(),
        options or {"stack_weight": "wfwhm", "is_spectral": False},
        library=inputs.library,
    )
    assert record is not None
    return record


def test_the_same_inputs_give_the_same_fingerprint(inputs: SimpleNamespace) -> None:
    """Building the record twice gives one fingerprint."""
    assert si.fingerprint_of(record_for(inputs)) == si.fingerprint_of(record_for(inputs))


def test_the_order_of_the_frames_does_not_matter(inputs: SimpleNamespace) -> None:
    """The same frames in another order are the same inputs."""
    assert si.fingerprint_of(record_for(inputs)) == si.fingerprint_of(
        record_for(inputs, frames=list(reversed(inputs.frames)))
    )


def test_an_added_frame_changes_the_fingerprint(inputs: SimpleNamespace, tmp_path: Path) -> None:
    """A new frame is a change."""
    extra = make_files(tmp_path, ["light_new.fits"])
    old = record_for(inputs)
    new = record_for(inputs, frames=[*inputs.frames, *extra])

    assert si.fingerprint_of(old) != si.fingerprint_of(new)
    assert si.describe_changes(old, new) == ["1 frame(s) added"]


def test_a_removed_frame_changes_the_fingerprint(inputs: SimpleNamespace) -> None:
    """A frame taken out is a change."""
    old = record_for(inputs)
    new = record_for(inputs, frames=inputs.frames[1:])

    assert si.describe_changes(old, new) == ["1 frame(s) removed"]


def test_a_replaced_frame_file_changes_the_fingerprint(inputs: SimpleNamespace) -> None:
    """A file with a new modification time is a different file."""
    old = record_for(inputs)
    stat = os.stat(inputs.frames[0])
    os.utime(inputs.frames[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    new = record_for(inputs)

    assert si.describe_changes(old, new) == ["1 frame(s) replaced"]


@pytest.mark.parametrize("kind", ["darks", "biases", "flats"])
def test_a_new_calibration_frame_changes_the_fingerprint(
    inputs: SimpleNamespace, tmp_path: Path, kind: str
) -> None:
    """A dark, bias or flat added to what the library picks is a change."""
    old = record_for(inputs)
    getattr(inputs.library, kind).extend(make_files(tmp_path, [f"extra_{kind}.fits"]))
    new = record_for(inputs)

    assert si.describe_changes(old, new) == [f"the {kind[:-1]} frames changed"]


def test_a_touched_calibration_file_changes_the_fingerprint(inputs: SimpleNamespace) -> None:
    """A flat replaced in place is a change even if the list is the same."""
    old = record_for(inputs)
    stat = os.stat(inputs.flats[0])
    os.utime(inputs.flats[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    assert si.fingerprint_of(old) != si.fingerprint_of(record_for(inputs))


@pytest.mark.parametrize("setting", sorted(DEFAULT_SETTINGS))
def test_each_setting_that_changes_a_stack_changes_the_fingerprint(
    inputs: SimpleNamespace, setting: str
) -> None:
    """Changing any one of the settings in the list moves the fingerprint."""
    changed_value = {"get_stack_rejection_sigma": (2.0, 2.5)}.get(setting)
    if changed_value is None:
        original = DEFAULT_SETTINGS[setting]
        changed_value = (not original) if isinstance(original, bool) else "something else"
    old = record_for(inputs)
    new = record_for(inputs, configuration=FakeConfiguration(**{setting: changed_value}))

    assert si.describe_changes(old, new) == [f"settings changed: {setting}"]


def test_an_option_given_directly_changes_the_fingerprint(inputs: SimpleNamespace) -> None:
    """Options passed to the stacker count as settings too."""
    old = record_for(inputs)
    new = record_for(inputs, options={"stack_weight": "noise", "is_spectral": False})

    assert si.describe_changes(old, new) == ["settings changed: options"]


def test_a_new_version_of_the_stacking_code_changes_the_fingerprint(
    inputs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising the algorithm version rebuilds every stack once."""
    old = record_for(inputs)
    monkeypatch.setattr(si, "STACKING_ALGORITHM_VERSION", si.STACKING_ALGORITHM_VERSION + 1)
    new = record_for(inputs)

    assert si.describe_changes(old, new) == ["the stacking code changed"]


def test_a_library_that_fails_gives_no_record() -> None:
    """If the inputs cannot be read there is no record, so no skip."""

    class BrokenLibrary:
        """A library whose lookups fail."""

        def get_dark_frames(self, **_: Any) -> list[str]:
            """Fail as an unreadable library would.

            Raises
            ------
            OSError
                Always.
            """
            raise OSError("library unreadable")

    record = si.build_stack_inputs_record(
        [make_frame("/fake/light.fits")], FakeConfiguration(), {}, library=BrokenLibrary()
    )

    assert record is None


def test_a_record_saved_next_to_a_stack_is_read_back(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """Writing and reading the record round-trips it with its fingerprint."""
    stack = str(tmp_path / "M_57_L_Stacked.fits")
    record = record_for(inputs)

    si.write_stack_inputs(stack, record)
    saved = si.read_stack_inputs(stack)

    assert saved is not None
    assert saved["fingerprint"] == si.fingerprint_of(record)
    assert saved["record"] == record
    assert si.inputs_file_path(stack).endswith("M_57_L_Stacked_inputs.json")


@pytest.mark.parametrize("content", ["not json", "[]", '{"fingerprint": "x"}'])
def test_a_damaged_record_reads_as_missing(tmp_path: Path, content: str) -> None:
    """A record that cannot be understood is the same as no record."""
    stack = str(tmp_path / "stack.fits")
    Path(si.inputs_file_path(stack)).write_text(content)

    assert si.read_stack_inputs(stack) is None


def test_a_missing_record_reads_as_missing(tmp_path: Path) -> None:
    """A stack that was made before records existed has none."""
    assert si.read_stack_inputs(str(tmp_path / "stack.fits")) is None


def make_stack_with_record(tmp_path: Path, inputs: SimpleNamespace) -> tuple[str, dict[str, Any]]:
    """Write a stack file and the record of the fixture's inputs beside it.

    Returns
    -------
    stack, record : `str`, `dict`
        The stack's path and the record saved for it.
    """
    stack = tmp_path / "stack.fits"
    stack.write_bytes(b"stack")
    record = record_for(inputs)
    si.write_stack_inputs(str(stack), record)
    return str(stack), record


def test_an_unchanged_stack_is_skipped(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """With the same fingerprint and the stack on disk, nothing is rebuilt."""
    stack, record = make_stack_with_record(tmp_path, inputs)

    decision = si.decide_whether_to_restack(stack, record)

    assert decision.skip
    assert decision.reasons == []


def test_a_changed_stack_is_rebuilt_and_the_reason_is_given(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """New frames lead to a rebuild that names them."""
    stack, _ = make_stack_with_record(tmp_path, inputs)
    extra = make_files(tmp_path, ["light_new.fits"])

    decision = si.decide_whether_to_restack(stack, record_for(inputs, frames=[*inputs.frames, *extra]))

    assert not decision.skip
    assert decision.reasons == ["1 frame(s) added"]


def test_a_missing_stack_file_is_rebuilt_even_with_a_record(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """The record alone is not a stack: if the file is gone, rebuild."""
    stack, record = make_stack_with_record(tmp_path, inputs)
    os.remove(stack)

    decision = si.decide_whether_to_restack(stack, record)

    assert not decision.skip
    assert decision.reasons == ["there is no stack yet"]


def test_a_stack_without_a_record_is_rebuilt(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """A stack made before records existed is rebuilt once."""
    stack = tmp_path / "stack.fits"
    stack.write_bytes(b"stack")

    decision = si.decide_whether_to_restack(str(stack), record_for(inputs))

    assert not decision.skip
    assert decision.reasons == ["the stack on disk has no record of its inputs"]


def test_force_always_rebuilds(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """``force`` wins over an unchanged record."""
    stack, record = make_stack_with_record(tmp_path, inputs)

    assert not si.decide_whether_to_restack(stack, record, force=True).skip


def test_the_environment_variable_forces_a_rebuild(
    tmp_path: Path, inputs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--force-restack`` reaches the worker processes this way."""
    stack, record = make_stack_with_record(tmp_path, inputs)
    monkeypatch.setenv(si.FORCE_RESTACK_ENVIRONMENT_VARIABLE, "1")

    assert not si.decide_whether_to_restack(stack, record).skip
    monkeypatch.setenv(si.FORCE_RESTACK_ENVIRONMENT_VARIABLE, "0")
    assert si.decide_whether_to_restack(stack, record).skip


def test_turning_the_setting_off_rebuilds(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """With ``skip_unchanged_stacks_enabled`` off, every stack is rebuilt."""
    stack, record = make_stack_with_record(tmp_path, inputs)

    assert not si.decide_whether_to_restack(stack, record, enabled=False).skip


def test_no_record_for_this_run_rebuilds(tmp_path: Path, inputs: SimpleNamespace) -> None:
    """When this run's inputs could not be read, nothing is skipped."""
    stack, _ = make_stack_with_record(tmp_path, inputs)

    assert not si.decide_whether_to_restack(stack, None).skip


def test_every_setting_the_stacking_code_reads_is_accounted_for() -> None:
    """A new setting in the stacking code must be listed here or it is missed.

    The skip compares only the settings in `SETTINGS_THAT_CHANGE_A_STACK`. If
    the stacking code starts to read another one, a change to it would leave
    stacks looking up to date. The test finds every ``get_...`` call on the
    configuration in the stacking code and requires each to be on one of the
    two lists in `stack_inputs.py`.
    """
    root = Path(si.__file__).resolve().parents[3]
    files = [
        *(root / "pipelines" / "stacking").rglob("*.py"),
        root / "drivers" / "siril_stacking_driver.py",
        root / "drivers" / "siril_interface.py",
    ]
    pattern = re.compile(r"(?:get_configuration\(\)|config|configuration)\.(get_[a-z_]+)\(")
    found: set[str] = set()
    for path in files:
        if "test" in path.parts[-2] or path.name.startswith("test_"):
            continue
        found.update(pattern.findall(path.read_text(encoding="utf-8")))
    known = set(si.SETTINGS_THAT_CHANGE_A_STACK) | set(si.SETTINGS_THAT_DO_NOT_CHANGE_A_STACK)

    assert found - known == set(), (
        f"The stacking code reads settings that stack_inputs.py does not list: {sorted(found - known)}. "
        "Add each to SETTINGS_THAT_CHANGE_A_STACK if it changes the stack's pixels, "
        "otherwise to SETTINGS_THAT_DO_NOT_CHANGE_A_STACK."
    )
