"""Tests of the Command Console's stop, selection, show and doc helpers.

They cover Ctrl+C stopping a running script or line (the workspace stays
usable), the read-only ``selection`` object that follows Image Processing,
``show(...)`` turning its arguments into a request to the app, and
``doc(...)`` finding one reference entry, several, or none, and a saved
script that uses a moved library name being told where it moved.
"""

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import Target
from backend.services.infrastructure import api_doc_generator
from backend.services.infrastructure.app_selection_service import AppSelection, AppSelectionService
from backend.services.infrastructure.console_helpers import (
    ConsoleSelection,
    ConsoleStar,
    make_show_request,
    show_argument_text,
)
from backend.services.infrastructure.scripting_runs import STOPPED_MESSAGE
from backend.services.infrastructure.scripting_service import ScriptingService

#: How long a test waits for a run to start or end, in seconds.
WAIT_SECONDS = 5.0


def _run_in_thread(
    service: ScriptingService, code: str, source: str
) -> tuple[threading.Thread, dict[str, Any]]:
    """Start a console run in its own thread, as the RPC worker pool does.

    Parameters
    ----------
    service : `ScriptingService`
        The console.
    code : `str`
        The code to run.
    source : `str`
        ``"editor"`` or ``"repl"``.

    Returns
    -------
    thread : `threading.Thread`
        The thread running the code.
    reply : `dict`
        Filled with the run's reply under ``"envelope"`` when it ends.
    """
    reply: dict[str, Any] = {}
    thread = threading.Thread(
        target=lambda: reply.update(envelope=service.execute_structured(code, source=source)), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + WAIT_SECONDS
    while not service._runs.running() and time.monotonic() < deadline:
        time.sleep(0.01)
    return thread, reply


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("counter = 0\nwhile True:\n    counter += 1\n    __import__('time').sleep(0.01)\n", "editor"),
        ("[__import__('time').sleep(0.01) for _ in iter(int, 1)]", "repl"),
    ],
)
def test_stop_interrupts_a_running_script_and_keeps_the_workspace(code: str, source: str) -> None:
    """Ctrl+C stops a looping run, says "Stopped." and keeps the console."""
    service = ScriptingService()
    service.execute_structured("kept = 41", source="repl")
    thread, reply = _run_in_thread(service, code, source)
    assert service._runs.running(), "the run did not start"

    assert service.stop_runs()["stopped"]
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive()

    envelope = reply["envelope"]
    assert envelope["status"] == "stopped"
    assert envelope["stdout"].endswith(STOPPED_MESSAGE + "\n")
    assert "KeyboardInterrupt" not in envelope["stderr"]
    assert service._runs.running() == []
    after = service.execute_structured("print(kept + 1)", source="repl")
    assert after["status"] == "success" and after["stdout"].strip() == "42"
    assert service.get_recent_runs(limit=2)[1]["status"] == "stopped"


def test_stop_with_nothing_running_stops_nothing() -> None:
    """A stop when no code runs changes nothing."""
    service = ScriptingService()
    assert service.stop_runs() == {"stopped": []}
    assert service.execute_structured("value = 1")["status"] == "success"


def _fake_library(offset_px: tuple[int, int]) -> SimpleNamespace:
    """Make a stand-in ``astrometrics`` object with one target, M 31.

    Parameters
    ----------
    offset_px : `tuple` [`int`, `int`]
        Where the processed copy of every stack starts in the stack.

    Returns
    -------
    astrometrics : `SimpleNamespace`
        ``targets.get`` finds M 31 only, and
        ``processing.astrometry.get_identification`` gives the offset.
    """
    m31 = Target(id="M_31", common_name="Andromeda Galaxy")
    identification = SimpleNamespace(viewer_offset_x=offset_px[0], viewer_offset_y=offset_px[1])
    return SimpleNamespace(
        targets=SimpleNamespace(get=lambda name: m31 if name.replace(" ", "_") == "M_31" else None),
        processing=SimpleNamespace(
            astrometry=SimpleNamespace(get_identification=lambda _path: identification)
        ),
    )


def test_selection_follows_image_processing_and_is_read_only() -> None:
    """``selection`` reads the latest pick each time and cannot be set."""
    selection_service = AppSelectionService()
    container = SimpleNamespace(app_selection_service=selection_service, astrometrics=_fake_library((0, 0)))
    service = ScriptingService(container)
    selection = service.console.locals["selection"]
    assert selection.target is None and selection.star is None
    assert "Nothing is selected" in repr(selection)

    selection_service.set_selection(
        target="M 31",
        path="/library/M_31/stack.fits",
        kind="stack",
        star={"id": "HD 3651", "name": "Vega", "ra": 279.23, "dec": 38.78, "x": 10.5, "y": 20.25},
    )
    assert isinstance(selection.target, Target) and selection.target.id == "M_31"
    assert selection.kind == "stack"
    assert selection.star.name == "Vega" and selection.star.ra_deg == pytest.approx(279.23)
    assert selection.star.x_px == pytest.approx(10.5) and selection.star.y_px == pytest.approx(20.25)
    assert selection.set_at is not None
    summary = service.execute_structured("selection", source="repl")["stdout"]
    assert "M 31" in summary and "/library/M_31/stack.fits" in summary and "Vega" in summary

    with pytest.raises(AttributeError):
        selection.target = "M 33"
    selection_service.set_selection(target="M 33", kind="unknown")
    assert selection.target is None and selection.kind is None and selection.star is None


def test_selection_star_is_in_one_based_pixels_of_the_shown_file() -> None:
    """A stack's star moves by the processed copy's crop; a frame's does not.

    The app sends 1-based pixels of the picture it shows. On a stack that
    picture is the processed copy, cropped here at (100, 40), so the star
    at (5, 7) on it is at (105, 47) in the stack file.
    """
    selection_service = AppSelectionService()
    container = SimpleNamespace(
        app_selection_service=selection_service, astrometrics=_fake_library((100, 40))
    )
    selection = ScriptingService(container).console.locals["selection"]
    star = {"id": "Vega", "name": "Vega", "x": 5.0, "y": 7.0}

    selection_service.set_selection(target="M 31", path="/library/M_31/stack.fits", kind="stack", star=star)
    assert (selection.star.x_px, selection.star.y_px) == (105.0, 47.0)
    assert "x_px 105.0  y_px 47.0" in repr(selection)

    selection_service.set_selection(target="M 31", path="/library/M_31/frame.fits", kind="frame", star=star)
    assert (selection.star.x_px, selection.star.y_px) == (5.0, 7.0)


def test_show_takes_the_selected_objects() -> None:
    """``show`` opens ``selection.target`` and ``selection.star`` as given."""
    assert show_argument_text(Target(id="M_31")) == "M_31"
    assert show_argument_text(ConsoleStar(id="HD 3651", name="Vega", library_id=None)) == "Vega"
    assert show_argument_text(ConsoleStar(id="HD 3651", name="Vega", library_id="star_7")) == "star_7"
    assert show_argument_text("M 13") == "M 13" and show_argument_text(None) is None
    with pytest.raises(TypeError):
        show_argument_text(3.5)


def test_a_saved_script_with_a_moved_name_says_where_it_moved() -> None:
    """A script that calls a renamed name prints "moved to" and the new call.

    The rename table keeps an old name for one release; a saved console
    script that still uses it fails with a message that names the new call.
    """
    from astrometricslib import TargetCatalog
    from astrometricslib._internal.renames import RENAMES

    catalog = object.__new__(TargetCatalog)
    service = ScriptingService(SimpleNamespace(astrometrics=SimpleNamespace(targets=catalog)))
    envelope = service.execute_structured(
        "target = 'M 31'\nframes = astrometrics.targets.select_frames(target)\n",
        source="editor",
        label="old_script.py",
    )
    assert envelope["status"] == "error"
    new_call = RENAMES["astrometricslib.TargetCatalog.select_frames"]
    assert f"astrometricslib.TargetCatalog.select_frames moved to {new_call}." in envelope["stderr"]


def test_set_selection_returns_plain_data() -> None:
    """The RPC reply is plain data the app can read."""
    stored = AppSelectionService().set_selection(target=" ", path="/a.fits", kind="frame", star={"x": 1})
    assert stored["target"] is None and stored["path"] == "/a.fits" and stored["kind"] == "frame"
    assert stored["star"] is None and stored["set_at"]


def _no_star(_text: str) -> tuple[str, str] | None:
    """Find no star, as in an empty library.

    Parameters
    ----------
    _text : `str`
        The star that was asked for.

    Returns
    -------
    star : `None`
        Always.
    """
    return None


def test_show_request_defaults_and_checks() -> None:
    """Show picks the display from its arguments and refuses bad ones."""
    empty = AppSelection()

    def vega(_text: str) -> tuple[str, str]:
        """Find Vega whatever is asked for.

        Returns
        -------
        star : `tuple` of `str`
            Vega's library id and name.
        """
        return "vega_record", "Vega"

    by_target = make_show_request(
        target="M 31", star=None, path=None, display=None, selection=empty, find_star=vega
    )
    assert by_target["display"] == "Image Processing" and by_target["target"] == "M 31"
    star_alone = make_show_request(
        target=None, star="Vega", path=None, display=None, selection=empty, find_star=vega
    )
    assert star_alone["display"] == "Astronomy Manager" and star_alone["star_id"] == "vega_record"
    star_on_target = make_show_request(
        target="M 57", star="Vega", path=None, display=None, selection=empty, find_star=vega
    )
    assert star_on_target["display"] == "Image Processing" and star_on_target["star_name"] == "Vega"
    picked = AppSelection(target="M 13")
    by_path = make_show_request(
        target=None, star=None, path="/s.fits", display=None, selection=picked, find_star=vega
    )
    assert by_path["target"] == "M 13" and by_path["path"] == "/s.fits"

    with pytest.raises(TypeError):
        make_show_request(target=None, star=None, path=None, display=None, selection=empty, find_star=vega)
    with pytest.raises(ValueError, match="target="):
        make_show_request(
            target=None, star=None, path="/s.fits", display=None, selection=empty, find_star=vega
        )
    with pytest.raises(ValueError, match="No star"):
        make_show_request(
            target=None, star="Nope", path=None, display=None, selection=empty, find_star=_no_star
        )
    with pytest.raises(ValueError, match="opens"):
        make_show_request(
            target="M 31", star=None, path=None, display="Planetarium", selection=empty, find_star=vega
        )
    with pytest.raises(ValueError, match="shows a star"):
        make_show_request(
            target="M 31", star=None, path=None, display="astronomy", selection=empty, find_star=vega
        )


def test_show_in_a_run_returns_an_app_request() -> None:
    """A run that calls show gives its request back to the window that ran."""
    service = ScriptingService()
    envelope = service.execute_structured('show(target="M 31")', source="repl")
    assert envelope["status"] == "success"
    assert envelope["app_requests"] == [
        {
            "kind": "show",
            "display": "Image Processing",
            "target": "M 31",
            "star_id": None,
            "star_name": None,
            "path": None,
        }
    ]
    assert "Opening M 31 in Image Processing." in envelope["stdout"]
    failed = service.execute_structured("show()", source="repl")
    assert failed["app_requests"] == [] and "TypeError" in failed["stderr"]


def test_selection_is_its_own_class() -> None:
    """The console's selection object is a `ConsoleSelection`."""
    assert isinstance(ScriptingService().console.locals["selection"], ConsoleSelection)


#: A small stand-in for the API reference index.
_ENTRIES = [
    {
        "topic_id": "api/astrometricslib",
        "anchor_id": "api-TargetFrames-get_rotation",
        "qualified_name": "TargetFrames.get_rotation",
    },
    {
        "topic_id": "api/astrometricslib",
        "anchor_id": "api-StellarCatalog-delete",
        "qualified_name": "StellarCatalog.delete",
    },
    {
        "topic_id": "api/astrometricslib",
        "anchor_id": "api-TargetCatalog-delete",
        "qualified_name": "TargetCatalog.delete",
    },
]


def _fake_find(name: str) -> list[dict[str, Any]]:
    """Look a name up in `_ENTRIES` the way the reference index does.

    Parameters
    ----------
    name : `str`
        The name.

    Returns
    -------
    entries : `list` of `dict`
        The entries that fit by full name or last part, or else the close
        names, marked as suggestions.
    """
    exact = [entry for entry in _ENTRIES if entry["qualified_name"] == name]
    matches = exact or [entry for entry in _ENTRIES if entry["qualified_name"].rsplit(".", 1)[-1] == name]
    if matches:
        return [{**entry, "suggestion": False} for entry in matches]
    close = [entry for entry in _ENTRIES if name.rstrip("z") in entry["qualified_name"]]
    return [{**entry, "suggestion": True} for entry in close]


@pytest.fixture
def fake_doc_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the reference index with `_ENTRIES`.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's patcher.
    """
    monkeypatch.setattr(api_doc_generator, "find_doc_entries", _fake_find)


@pytest.mark.usefixtures("fake_doc_index")
def test_doc_with_one_match_asks_the_app_to_open_it() -> None:
    """Doc with a name that fits one entry returns a request to open it."""
    service = ScriptingService()
    envelope = service.execute_structured('doc("get_rotation")', source="repl")
    assert envelope["app_requests"] == [
        {
            "kind": "doc",
            "topic_id": "api/astrometricslib",
            "anchor_id": "api-TargetFrames-get_rotation",
            "qualified_name": "TargetFrames.get_rotation",
        }
    ]


@pytest.mark.usefixtures("fake_doc_index")
def test_doc_follows_an_object_to_its_class() -> None:
    """Doc of an object's method finds that class's entry only."""
    service = ScriptingService()
    service.execute_structured(
        "class TargetCatalog:\n    def delete(self):\n        pass\ncatalog = TargetCatalog()\n"
    )
    by_text = service.execute_structured('doc("catalog.delete")', source="repl")
    by_object = service.execute_structured("doc(catalog.delete)", source="repl")
    for envelope in (by_text, by_object):
        assert [request["qualified_name"] for request in envelope["app_requests"]] == ["TargetCatalog.delete"]


@pytest.mark.usefixtures("fake_doc_index")
def test_doc_with_several_or_no_matches_prints_and_opens_nothing() -> None:
    """A name in several classes lists them; an unknown one suggests others."""
    service = ScriptingService()
    several = service.execute_structured('doc("delete")', source="repl")
    assert several["app_requests"] == []
    assert "1. doc StellarCatalog.delete" in several["stdout"]
    assert "2. doc TargetCatalog.delete" in several["stdout"]
    none = service.execute_structured('doc("get_rotationz")', source="repl")
    assert none["app_requests"] == []
    assert "No documentation found for get_rotationz." in none["stdout"]
    assert "TargetFrames.get_rotation" in none["stdout"]


def test_help_lists_the_new_names_and_terminal_commands() -> None:
    """Help names the new helpers and the clear commands, in short lines."""
    text = ScriptingService().execute_structured("help", source="repl")["stdout"]
    for name in ("selection", "show", "doc", "clear('workspace')"):
        assert name in text
    assert all(len(line) <= 78 for line in text.splitlines() if not line.startswith("Example"))


def test_doc_with_the_real_reference_index() -> None:
    """The real reference index finds TargetFrames.get_rotation."""
    service = ScriptingService(SimpleNamespace(astrometrics=None))
    envelope = service.execute_structured('doc("TargetFrames.get_rotation")', source="repl")
    assert [request["qualified_name"] for request in envelope["app_requests"]] == [
        "TargetFrames.get_rotation"
    ]
    assert envelope["app_requests"][0]["anchor_id"] == "api-TargetFrames-get_rotation"
