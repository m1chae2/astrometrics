"""Unit tests for the ScriptingService and Command Console backend methods.

Verifies workspace manifest extraction, execution envelope generation,
recipe discovery and retrieval, user script persistence, documentation
topic reading, and editor buffer synchronization.
"""

from pathlib import Path

import pytest

from astrometricslib import InvalidArgumentError
from backend.services.infrastructure.scripting_service import ScriptingService, inspect_api


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
