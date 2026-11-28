"""Tests for the rename table and the "moved to ..." error.

Reading a renamed method on a sub-API object (or on `Astrometrics`) raises
`MovedError` with the new call; any other missing name is a plain
`AttributeError`. The table starts empty; later steps of issue #206 fill it.
"""

import pytest

import astrometricslib
from astrometricslib._internal import renames
from astrometricslib._internal.renames import RENAMES, MovedNames
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.api.jobs import Jobs
from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.api.quality import QualityRecords
from astrometricslib.api.stars import StellarCatalog
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.api.visualization import Visualization
from astrometricslib.exceptions import MovedError

SUB_API_CLASSES = (
    astrometricslib.Astrometrics,
    TargetCatalog,
    StellarCatalog,
    ProcessingPipelines,
    CalibrationCatalog,
    QualityRecords,
    Visualization,
    Jobs,
)


@pytest.fixture
def moved_select_frames(monkeypatch: pytest.MonkeyPatch) -> str:
    """Put one entry in the rename table for the length of a test.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Restores the table afterwards.

    Returns
    -------
    new_call : `str`
        The new call text of the entry.
    """
    new_call = "astrometrics.targets.frames.query(target)"
    monkeypatch.setitem(RENAMES, "astrometricslib.TargetCatalog.select_framez", new_call)
    return new_call


@pytest.mark.parametrize("sub_api", SUB_API_CLASSES)
def test_every_sub_api_class_reads_the_rename_table(sub_api: type) -> None:
    """Each sub-API class, and the root, inherits the hook."""
    assert issubclass(sub_api, MovedNames)


def test_a_moved_name_raises_moved_error_with_the_new_call(moved_select_frames: str) -> None:
    """The message says where the method moved to."""
    catalog = TargetCatalog.__new__(TargetCatalog)
    with pytest.raises(MovedError, match="moved to") as caught:
        _ = catalog.select_framez
    assert caught.value.new_call == moved_select_frames
    assert caught.value.old_name == "astrometricslib.TargetCatalog.select_framez"
    assert caught.value.report == "error"


def test_a_moved_name_counts_as_missing(moved_select_frames: str) -> None:
    """`hasattr` and `getattr` with a default see the old name as absent."""
    catalog = TargetCatalog.__new__(TargetCatalog)
    assert not hasattr(catalog, "select_framez")
    assert getattr(catalog, "select_framez", None) is None


def test_another_missing_name_is_a_plain_attribute_error() -> None:
    """A name that was never public gets the usual error."""
    catalog = TargetCatalog.__new__(TargetCatalog)
    with pytest.raises(AttributeError) as caught:
        _ = catalog.no_such_method
    assert not isinstance(caught.value, MovedError)
    assert "no_such_method" in str(caught.value)


def test_a_name_that_exists_is_never_looked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook runs only when normal lookup fails."""
    monkeypatch.setitem(RENAMES, "astrometricslib.TargetCatalog.get", "nowhere")
    assert callable(TargetCatalog.__new__(TargetCatalog).get)


def test_the_table_maps_text_to_text() -> None:
    """Every entry has a library-qualified key and a non-empty new call."""
    assert renames.RENAMES is RENAMES
    for old, new in RENAMES.items():
        assert old.startswith("astrometricslib.") and old.count(".") == 2, old
        assert new.strip(), old


@pytest.mark.parametrize(
    ("module_name", "name"),
    [
        ("astrometricslib.drivers", "LibraryFolders"),
        ("astrometricslib.parameters", "run_tuning"),
    ],
)
def test_a_name_a_host_module_dropped_says_where_it_went(module_name: str, name: str) -> None:
    """Importing a dropped host-module name raises with the new call."""
    import importlib

    module = importlib.import_module(module_name)
    with pytest.raises(ImportError, match="moved to"):
        getattr(module, name)
    assert not hasattr(module, "no_such_name")


def test_a_from_import_of_a_dropped_name_keeps_the_message() -> None:
    """``from ... import`` of a dropped name says where it went, too."""
    with pytest.raises(ImportError, match="moved to"):
        from astrometricslib.drivers import LibraryFolders  # ruff: ignore[unused-import]
