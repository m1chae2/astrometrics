"""Unit tests for the ScriptingService and Command Console backend methods.

Verifies workspace manifest extraction, execution envelope generation,
recipe discovery and retrieval, user script persistence, documentation
topic reading, editor buffer synchronization, and job loading.
"""

from pathlib import Path
from unittest.mock import MagicMock

from astrometricslib import ProcessingJob
from backend.services.infrastructure.scripting_service import ScriptingService


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


def test_scripting_service_editor_sync() -> None:
    """Verify editor buffer getting and setting."""
    service = ScriptingService()
    res = service.set_editor_buffer("x = 100\nprint(x)")
    assert res["status"] == "success"

    buf = service.get_editor_buffer()
    assert buf["code"] == "x = 100\nprint(x)"


def test_scripting_service_load_job_into_scope() -> None:
    """Verify loading a ProcessingJob into console locals."""
    mock_container = MagicMock()
    mock_job_service = MagicMock()
    mock_target_service = MagicMock()

    job = ProcessingJob(
        id="job-12345",
        target_id="M 42",
        job_type="stacking",
        status="completed",
        input_metrics={"snr_estimate": 14.5},
        output_metrics={"stacked_fwhm": 2.1},
    )
    mock_job_service.get_job.return_value = job

    mock_target = MagicMock()
    mock_target.id = "M 42"
    mock_target_service.get_targets.return_value = mock_target

    mock_container.job_service = mock_job_service
    mock_container.target_service = mock_target_service

    service = ScriptingService(container=mock_container)
    load_res = service.load_job_into_scope("job-12345")

    assert load_res["job_id"] == "job-12345"
    assert load_res["target_name"] == "M 42"
    assert "run" in service.console.locals
    assert service.console.locals["run"].id == "job-12345"
    assert "target" in service.console.locals
