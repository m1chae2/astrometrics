"""Unit tests for the ScriptingService and Command Console backend methods.

Verifies workspace manifest extraction, execution envelope generation,
recipe discovery and retrieval, user script persistence, documentation
topic reading, and editor buffer synchronization.
"""

from pathlib import Path

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from backend.services.infrastructure.scripting_runs import RECENT_RUN_LIMIT, RECENT_RUN_TEXT_LIMIT
from backend.services.infrastructure.scripting_service import ScriptingService
from backend.services.infrastructure.scripting_workspace import inspect_api


def test_scripting_service_workspace_manifest() -> None:
    """Verify get_workspace_manifest reports numpy array shape and dtype."""
    service = ScriptingService()
    service.execute("my_arr = np.zeros((10, 20), dtype=np.float32)")
    manifest = service.get_workspace_manifest()

    var_entry = next((item for item in manifest if item["name"] == "my_arr"), None)
    assert var_entry is not None
    assert var_entry["type"] == "ndarray"
    assert var_entry["shape"] == "(10, 20)"
    assert var_entry["dtype"] == "float32"


def test_scripting_service_execute_structured_plot() -> None:
    """Verify execute_structured captures plots and returns success status."""
    service = ScriptingService()
    result = service.execute_structured("""
import matplotlib.pyplot as plt
fig = plt.figure()
plt.plot([1, 2, 3], [4, 5, 6])
result = 42
""")
    assert result["status"] == "success"
    assert result["result"] == 42
    assert len(result["plots"]) >= 1
    assert "interactive_plots" in result
    assert len(result["interactive_plots"]) >= 1
    fig_id = result["interactive_plots"][0]["figure_id"]
    assert service.get_figure_manager(fig_id) is not None


def test_scripting_service_reports_figure_size_in_pixels() -> None:
    """Each interactive plot carries the figure's size, so its window fits."""
    service = ScriptingService()
    result = service.execute_structured("""
import matplotlib.pyplot as plt
fig = plt.figure(figsize=(8, 5), dpi=100)
plt.plot([1, 2, 3], [4, 5, 6])
""")
    plot = result["interactive_plots"][-1]
    assert (plot["width"], plot["height"]) == (800, 500)
    assert isinstance(plot["width"], int)


def test_scripting_service_list_and_get_recipes() -> None:
    """Verify built-in script recipes can be listed and retrieved."""
    service = ScriptingService()
    recipes = service.list_recipes()
    assert len(recipes) > 0

    first_recipe = recipes[0]
    recipe_data = service.get_recipe(first_recipe["id"])
    assert recipe_data["id"] == first_recipe["id"]
    assert len(recipe_data["code"]) > 0


def test_scripting_service_user_scripts(tmp_path: Path) -> None:
    """Verify user script save, list, and read operations."""
    service = ScriptingService()
    # Mock user scripts dir to isolate in tmp_path
    service._get_user_scripts_dir = lambda: tmp_path

    saved = service.save_user_script("test_custom.py", "print('hello test')")
    assert saved["status"] == "saved"

    scripts = service.list_user_scripts()
    assert any(s["filename"] == "test_custom.py" for s in scripts)

    read_data = service.read_user_script("test_custom.py")
    assert read_data["code"] == "print('hello test')"


def test_save_user_script_broadcasts_scripts_changed(tmp_path: Path) -> None:
    """Verify that saving a script sends a ``scripts:changed`` event."""
    sent: list[tuple[str, dict]] = []

    class FakeSocketManager:
        """Record the UI events the service broadcasts."""

        def broadcast_ui_event_sync(self, action: str, payload: dict) -> None:
            """Store one broadcast event."""
            sent.append((action, payload))

    class FakeContainer:
        """Offer only the socket manager the service needs."""

        socket_manager = FakeSocketManager()

    service = ScriptingService(container=FakeContainer())
    service._get_user_scripts_dir = lambda: tmp_path
    service.save_user_script("fresh_script", "print(1)")
    assert ("scripts:changed", {"filename": "fresh_script.py"}) in sent


def test_scripting_service_docs() -> None:
    """Verify documentation topics listing and retrieval."""
    service = ScriptingService()
    topics = service.list_doc_topics()
    assert len(topics) > 0

    first_topic = topics[0]
    topic_data = service.get_doc_topic(first_topic["id"])
    assert len(topic_data["content"]) > 0


def test_scripting_service_search_docs_matches_title_and_body() -> None:
    """Verify search_doc_topics matches on title and on body text."""
    service = ScriptingService()

    all_topics = service.list_doc_topics()
    assert len(all_topics) > 0

    # A blank query returns every topic, unfiltered.
    assert service.search_doc_topics("   ") == all_topics

    # Matching a known title should find that topic without a snippet,
    # since the match is in the title, not the body.
    first_topic = all_topics[0]
    title_matches = service.search_doc_topics(first_topic["title"][:6])
    assert any(t["id"] == first_topic["id"] for t in title_matches)
    matched_topic = next(t for t in title_matches if t["id"] == first_topic["id"])
    assert "snippet" not in matched_topic

    # Matching text that only appears in a topic's body should return
    # that topic with a snippet showing where the match was found.
    topic_data = service.get_doc_topic(first_topic["id"])
    body_only_phrase = topic_data["content"].strip().splitlines()[-1].strip()
    if body_only_phrase and body_only_phrase not in first_topic["title"]:
        body_matches = service.search_doc_topics(body_only_phrase)
        found = next((t for t in body_matches if t["id"] == first_topic["id"]), None)
        assert found is not None
        assert "snippet" in found


def test_scripting_service_reset_workspace_drops_user_variables() -> None:
    """Verify reset_workspace clears user variables but keeps built-ins."""
    service = ScriptingService()
    service.execute("my_custom_variable = 12345")
    assert "my_custom_variable" in service.console.locals

    manifest = service.reset_workspace()

    assert "my_custom_variable" not in service.console.locals
    assert "np" in service.console.locals
    assert not any(item["name"] == "my_custom_variable" for item in manifest)


def test_scripting_service_editor_sync() -> None:
    """Verify editor buffer getting and setting."""
    service = ScriptingService()
    res = service.set_editor_buffer("x = 100\nprint(x)")
    assert res["status"] == "success"

    buf = service.get_editor_buffer()
    assert buf["code"] == "x = 100\nprint(x)"


def test_scripting_service_api_docs() -> None:
    """Verify Python API documentation is available and properly formatted."""
    service = ScriptingService()
    topics = service.list_doc_topics()

    # Verify API Reference topics are present
    api_topics = [t for t in topics if t.get("category") == "API Reference"]
    assert len(api_topics) >= 3

    topic_ids = [t["id"] for t in api_topics]
    assert "api/index" in topic_ids
    assert "api/astrometricslib" in topic_ids
    assert "api/wayfindinglib" in topic_ids

    # Verify resolving API index
    index_doc = service.get_doc_topic("api/index")
    assert "Python API Reference" in index_doc["title"]
    assert "astrometricslib" in index_doc["content"]
    assert "wayfindinglib" in index_doc["content"]

    # Verify resolving astrometricslib with and without .rst extension
    astro_doc = service.get_doc_topic("api/astrometricslib.rst")
    assert "astrometricslib" in astro_doc["title"]
    assert "Astrometrics" in astro_doc["content"]
    assert "TargetCatalog" in astro_doc["content"]

    # Verify resolving wayfindinglib
    way_doc = service.get_doc_topic("api/wayfindinglib.rst")
    assert "wayfindinglib" in way_doc["title"]
    assert "Wayfinder" in way_doc["content"]
    assert "ObservatoryControl" in way_doc["content"]

    # Verify resolving individual class generated stub
    stub_doc = service.get_doc_topic("generated/astrometricslib.Astrometrics.rst")
    assert "Astrometrics" in stub_doc["title"]
    assert "sub-APIs" in stub_doc["content"]


def test_get_figure_manager_provides_toolbar() -> None:
    """Verify figure managers from scripting service have an active toolbar."""
    service = ScriptingService()
    plt = service.console.locals.get("plt")
    fig = plt.figure(99)
    plt.plot([0, 1], [0, 1])

    mgr = service.get_figure_manager(99)
    assert mgr is not None
    assert mgr.toolbar is not None
    assert mgr.canvas.toolbar is not None

    # Test toggling zoom mode via WebAgg toolbar event
    mgr.handle_json({"type": "toolbar_button", "name": "zoom"})
    assert mgr.toolbar.mode == "zoom rect"

    plt.close(fig)


def test_inspect_api_rejects_none() -> None:
    """inspect_api raises InvalidArgumentError when given None."""
    with pytest.raises(InvalidArgumentError, match="no object to inspect"):
        inspect_api(None)


def test_recent_runs_newest_first_with_label_and_limit() -> None:
    """The history lists runs newest first and honors the limit."""
    service = ScriptingService()
    service.execute_structured("print('first')\nx = 1", source="editor", label="first.py")
    service.execute_structured("print('second')\ny = 2")
    runs = service.get_recent_runs(limit=2)
    assert [run["label"] for run in runs] == ["print('second')", "first.py"]
    assert runs[1]["stdout"].strip() == "first"
    assert runs[0]["status"] == "success"
    assert runs[0]["ran_at"].endswith("+00:00")
    assert len(service.get_recent_runs()) == 1
    assert "workspace" not in runs[0]


def test_recent_runs_reject_bad_limits() -> None:
    """A limit outside 1 to 20 is refused."""
    service = ScriptingService()
    for bad in (0, 21, -1):
        with pytest.raises(InvalidArgumentError):
            service.get_recent_runs(limit=bad)


def test_recent_runs_keep_errors_and_cut_long_text() -> None:
    """An error run keeps its traceback, and long text is cut with a note."""
    service = ScriptingService()
    service.execute_structured("raise ValueError('boom')\n# more")
    service.execute_structured(f"print('a' * {RECENT_RUN_TEXT_LIMIT + 50})")
    long_run, error_run = service.get_recent_runs(limit=2)
    assert error_run["status"] == "error"
    assert "boom" in error_run["stderr"]
    assert "more characters were cut" in long_run["stdout"]
    assert len(long_run["stdout"]) < RECENT_RUN_TEXT_LIMIT + 100


def test_recent_runs_history_is_bounded_and_files_follow_it() -> None:
    """Figure files stay while their run is kept and go when it drops out."""
    service = ScriptingService()
    code = "plt.close('all')\nplt.figure()\nplt.plot([1, 2, 3])"
    service.execute_structured(code)
    first = service.get_recent_runs()[0]
    assert len(first["plot_paths"]) == 1
    first_file = Path(first["plot_paths"][0])
    assert first_file.is_file()
    for _ in range(RECENT_RUN_LIMIT - 1):
        service.execute_structured("pass")
    assert first_file.is_file()
    service.execute_structured("pass")
    assert len(service.get_recent_runs(limit=RECENT_RUN_LIMIT)) == RECENT_RUN_LIMIT
    assert not first_file.exists()
    service.execute_structured("plt.close('all')")


def test_recent_runs_rpc_method_is_declared_and_served() -> None:
    """The RPC method is declared, handled by the service, and read-only."""
    from backend.public_interface import RPC_METHODS
    from backend.routers import rpc_router

    assert "terminal:get_recent_runs" in RPC_METHODS
    assert rpc_router._HANDLERS["terminal:get_recent_runs"] == ("scripting_service", "get_recent_runs")
    assert hasattr(ScriptingService, "get_recent_runs")


class _RecordingSocketManager:
    """Stand-in socket manager that records the events it is asked to send."""

    def __init__(self) -> None:
        """Start with no recorded events."""
        self.events: list[tuple[str, dict]] = []

    def broadcast_ui_event_sync(self, event: str, payload: dict) -> None:
        """Record one broadcast event."""
        self.events.append((event, payload))


class _FakeContainer:
    """Stand-in container that holds only a socket manager."""

    def __init__(self) -> None:
        """Attach a recording socket manager."""
        self.socket_manager = _RecordingSocketManager()


_ONE_FIGURE_CODE = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([1, 2, 3])
"""


def _plot_events(container: _FakeContainer) -> list[dict]:
    """List the payloads of the recorded plot broadcasts.

    Parameters
    ----------
    container : `_FakeContainer`
        The container whose socket manager recorded the events.

    Returns
    -------
    payloads : `list[dict]`
        One payload per ``terminal:plot_generated`` event.
    """
    return [
        payload for event, payload in container.socket_manager.events if event == "terminal:plot_generated"
    ]


def test_console_runs_do_not_broadcast_plots() -> None:
    """A console's own Run or REPL returns plots but sends no event."""
    import matplotlib.pyplot as plt

    plt.close("all")
    container = _FakeContainer()
    service = ScriptingService(container=container)
    for source in ("editor", "repl"):
        result = service.execute_structured(_ONE_FIGURE_CODE, source=source)
        assert len(result["interactive_plots"]) == 1
        plt.close("all")
    assert _plot_events(container) == []


def test_agent_runs_broadcast_plots() -> None:
    """A run from an agent tells the open windows about its figure."""
    import matplotlib.pyplot as plt

    plt.close("all")
    container = _FakeContainer()
    service = ScriptingService(container=container)
    result = service.execute_structured(_ONE_FIGURE_CODE, source="agent")
    plt.close("all")
    assert len(_plot_events(container)) == len(result["interactive_plots"]) == 1


def test_second_run_does_not_capture_the_first_runs_figure() -> None:
    """A figure left open by one run is not captured by the next run."""
    import matplotlib.pyplot as plt

    plt.close("all")
    service = ScriptingService()
    first = service.execute_structured(_ONE_FIGURE_CODE)
    second = service.execute_structured("value = 1")
    assert len(first["interactive_plots"]) == 1
    assert second["interactive_plots"] == []
    assert second["plots"] == []
    assert service.get_recent_runs(limit=1)[0]["plot_paths"] == []
    redraw = service.execute_structured("plt.figure(1).axes[0].plot([3, 2, 1])")
    plt.close("all")
    assert [item["figure_id"] for item in redraw["interactive_plots"]] == [1]


def test_console_preloads_only_the_public_objects() -> None:
    """The console holds the public library objects, not the app's services."""
    from types import SimpleNamespace

    container = SimpleNamespace(
        astrometrics=object(),
        wayfinder=object(),
        target_service=object(),
        job_service=object(),
        stellar_object_service=object(),
        telescope_service=object(),
    )
    service = ScriptingService(container)
    names = {name for name in service.console.locals if not name.startswith("_")}
    assert names == {
        "astrometrics",
        "wayfinder",
        "np",
        "plt",
        "fits",
        "help",
        "list_commands",
        "inspect_api",
        "doc",
        "show",
        "selection",
    }
    assert service.console.locals["astrometrics"] is container.astrometrics
    assert service.get_workspace_manifest() == []
    help_text = service.execute("help")
    assert "astrometrics" in help_text and "fits" in help_text
    assert "target_service" not in help_text and "job_service" not in help_text


def test_preloaded_objects_name_their_types() -> None:
    """Each preloaded object comes with its type and whether it is there."""
    service = ScriptingService()
    objects = {entry["name"]: entry for entry in service.get_preloaded_objects()}
    assert list(objects) == ["astrometrics", "wayfinder", "np", "plt", "fits", "selection", "show", "doc"]
    assert objects["show"]["type"] == "function" and objects["selection"]["type"] == "ConsoleSelection"
    assert objects["np"]["type"] == "numpy" and objects["np"]["available"]
    assert objects["fits"]["type"] == "astropy.io.fits"
    assert objects["astrometrics"]["available"] is False
    assert all(entry["description"] for entry in objects.values())


def test_help_with_an_object_prints_only_its_public_api() -> None:
    """help(obj) gives the docstring and public methods, no private names."""
    service = ScriptingService()
    service.execute(
        "class Sample:\n"
        '    """A sample object."""\n'
        "    def measure(self, size):\n"
        '        """Measure something."""\n'
        "    def _hidden(self):\n"
        '        """Private."""\n'
        "sample = Sample()\n"
    )
    text = service.execute("help(sample)")
    assert "A sample object." in text
    assert "measure(size)" in text and "Measure something." in text
    assert "_hidden" not in text


def test_recipes_include_the_tutorial_notebooks_as_scripts() -> None:
    """Each tutorial notebook is a recipe, turned into a console script."""
    service = ScriptingService()
    recipes = {recipe["id"]: recipe for recipe in service.list_recipes()}
    notebook = recipes["08_visualization"]
    assert notebook["kind"] == "notebook"
    assert notebook["category"] == "astrometrics"
    assert notebook["name"] == "8. Visualization"
    assert recipes["tune_parameters"]["kind"] == "script"
    assert recipes["remote_image_ingestion"]["category"] == "wayfinding"

    recipe = service.get_recipe("08_visualization")
    assert recipe["filename"] == "08_visualization.py"
    compile(recipe["code"], recipe["filename"], "exec")
    code_lines = [line.strip() for line in recipe["code"].splitlines()]
    assert "astrometrics = Astrometrics()" not in code_lines
    assert any(line.startswith("# astrometrics = Astrometrics()") for line in code_lines)
