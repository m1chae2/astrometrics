"""Tests of the Command Console's stop, selection, show and doc helpers.

They cover Ctrl+C stopping a running script or line (the workspace stays
usable), the read-only ``selection`` object that follows Image Processing,
``show(...)`` turning its arguments into a request to the app, and
``doc(...)`` finding one reference entry, several, or none.
"""

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.infrastructure import api_doc_generator
from backend.services.infrastructure.app_selection_service import AppSelection, AppSelectionService
from backend.services.infrastructure.console_helpers import ConsoleSelection, make_show_request
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


def test_selection_follows_image_processing_and_is_read_only() -> None:
    """``selection`` reads the latest pick each time and cannot be set."""
    selection_service = AppSelectionService()
    service = ScriptingService(SimpleNamespace(app_selection_service=selection_service))
    selection = service.console.locals["selection"]
    assert selection.target is None and selection.star is None
    assert "Nothing is selected" in repr(selection)

    selection_service.set_selection(
        target="M 31",
        path="/library/M_31/stack.fits",
        kind="stack",
        star={"id": "HD 3651", "name": "Vega", "ra": 279.23, "dec": 38.78, "x": 10.5, "y": 20.25},
    )
    assert selection.target == "M 31" and selection.kind == "stack"
    assert selection.star.name == "Vega" and selection.star.x == pytest.approx(10.5)
    assert selection.set_at is not None
    summary = service.execute_structured("selection", source="repl")["stdout"]
    assert "M 31" in summary and "/library/M_31/stack.fits" in summary and "Vega" in summary

    with pytest.raises(AttributeError):
        selection.target = "M 33"
    selection_service.set_selection(target="M 33", kind="unknown")
    assert selection.target == "M 33" and selection.kind is None and selection.star is None


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
        "anchor_id": "api-TargetCatalog-delete_images",
        "qualified_name": "TargetCatalog.delete_images",
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
    envelope = service.execute_structured('doc("delete_images")', source="repl")
    assert envelope["app_requests"] == [
        {
            "kind": "doc",
            "topic_id": "api/astrometricslib",
            "anchor_id": "api-TargetCatalog-delete_images",
            "qualified_name": "TargetCatalog.delete_images",
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
    none = service.execute_structured('doc("delete_imagez")', source="repl")
    assert none["app_requests"] == []
    assert "No documentation found for delete_imagez." in none["stdout"]
    assert "TargetCatalog.delete_images" in none["stdout"]


def test_help_lists_the_new_names_and_terminal_commands() -> None:
    """Help names the new helpers and the clear commands, in short lines."""
    text = ScriptingService().execute_structured("help", source="repl")["stdout"]
    for name in ("selection", "show", "doc", "clear('workspace')"):
        assert name in text
    assert all(len(line) <= 78 for line in text.splitlines() if not line.startswith("Example"))


def test_doc_with_the_real_reference_index() -> None:
    """The real reference index finds TargetCatalog.delete_images."""
    service = ScriptingService(SimpleNamespace(astrometrics=None))
    envelope = service.execute_structured('doc("TargetCatalog.delete_images")', source="repl")
    assert [request["qualified_name"] for request in envelope["app_requests"]] == [
        "TargetCatalog.delete_images"
    ]
    assert envelope["app_requests"][0]["anchor_id"] == "api-TargetCatalog-delete_images"
