"""Purpose: Tests of the Python API reference the backend builds.

Description: The reference is made from the libraries' public names. These
tests check that the public modules are covered and that no private name
is listed.
"""

from backend.services.infrastructure.api_doc_generator import (
    generate_astrometricslib_docs,
    generate_wayfindinglib_docs,
    get_api_topic_content,
)


def test_the_astrometricslib_reference_lists_its_public_modules() -> None:
    """Each public module has a block, with its names."""
    text = generate_astrometricslib_docs()

    for module in ("exceptions", "reports", "jobs", "drivers"):
        assert f"### `astrometricslib.{module}`" in text
    assert "`NotFoundError`" in text
    assert "`observing_night_id" in text
    assert "_internal" not in text.split("## Public Modules")[1]


def test_the_wayfindinglib_reference_lists_its_public_modules() -> None:
    """The errors and report states appear, and the sub-APIs are covered."""
    text = generate_wayfindinglib_docs()

    assert "### `wayfindinglib.exceptions`" in text
    assert "### `wayfindinglib.reports`" in text
    assert "`RunState`" in text
    assert "### `control.mount`" in text
    assert "_internal" not in text


def test_the_topic_lookup_serves_both_references() -> None:
    """Both library pages can be fetched by their topic ids."""
    for topic in ("api/astrometricslib", "api/wayfindinglib"):
        page = get_api_topic_content(topic)
        assert page is not None
        assert page["content"].startswith("# ")


def test_signatures_show_short_type_names() -> None:
    """A signature names a class by its short name, not its module path."""
    import astrometricslib
    from backend.services.infrastructure.api_doc_generator import format_signature, short_type_names

    signature = format_signature(astrometricslib.TargetCatalog.delete_images)

    assert "-> ImageDeletionResult" in signature
    assert "Target | None" in signature
    assert "paths: list[str]" in signature
    assert "astrometricslib." not in signature
    assert short_type_names("dict[str, numpy.ndarray] | pathlib.Path") == "dict[str, ndarray] | Path"


def test_signatures_keep_default_values() -> None:
    """Only types are shortened: a default value with dots is kept."""
    from backend.services.infrastructure.api_doc_generator import format_signature

    def sample(name: str = "a.b.C") -> None:
        """Take a name with dots in its default value."""

    assert format_signature(sample) == "(name: str = 'a.b.C') -> None"


def test_reference_signatures_have_no_module_paths() -> None:
    """No signature in either reference prints a module path."""
    import re

    text = generate_astrometricslib_docs() + generate_wayfindinglib_docs()
    signature_lines = [line for line in text.splitlines() if line.startswith(("#### `", "- **`"))]

    assert signature_lines
    for line in signature_lines:
        signature = line.split("{#")[0].split("(", 1)[-1]
        assert not re.search(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*", signature), line


def test_every_entry_has_a_unique_anchor() -> None:
    """Each documented entry has an anchor, once per topic."""
    from collections import Counter

    from backend.services.infrastructure.api_doc_generator import api_doc_entries

    entries = api_doc_entries()
    anchors = {entry["anchor_id"] for entry in entries}

    assert "api-TargetCatalog-delete_images" in anchors
    assert "api-astrometricslib-capture_job_logs" in anchors
    assert "api-exceptions-NotFoundError" in anchors
    for topic_id in ("api/astrometricslib", "api/wayfindinglib"):
        counts = Counter(entry["anchor_id"] for entry in entries if entry["topic_id"] == topic_id)
        assert counts, topic_id
        assert [anchor for anchor, count in counts.items() if count > 1] == []
    page = get_api_topic_content("api/astrometricslib")
    assert page is not None
    assert "`TargetCatalog.delete_images(" in page["content"]
    assert "{#api-TargetCatalog-delete_images}" in page["content"]


def test_find_doc_entries_by_bare_name() -> None:
    """A bare name finds every class that has a method of that name."""
    from backend.services.infrastructure.api_doc_generator import find_doc_entries

    assert find_doc_entries("delete_images") == [
        {
            "topic_id": "api/astrometricslib",
            "anchor_id": "api-TargetCatalog-delete_images",
            "qualified_name": "TargetCatalog.delete_images",
            "suggestion": False,
        }
    ]
    owners = {entry["qualified_name"] for entry in find_doc_entries("get")}
    assert {"TargetCatalog.get", "StellarCatalog.get"} <= owners


def test_find_doc_entries_by_owner_and_name() -> None:
    """An owner and a name finds that one entry."""
    from backend.services.infrastructure.api_doc_generator import find_doc_entries

    entries = find_doc_entries("TargetCatalog.get")

    assert [entry["anchor_id"] for entry in entries] == ["api-TargetCatalog-get"]
    assert entries[0]["suggestion"] is False


def test_find_doc_entries_by_console_path() -> None:
    """A path through the console's preloaded objects finds its class."""
    from backend.services.infrastructure.api_doc_generator import find_doc_entries

    by_method = find_doc_entries("astrometrics.targets.delete_images")
    by_object = find_doc_entries("astrometrics.targets")

    assert [entry["anchor_id"] for entry in by_method] == ["api-TargetCatalog-delete_images"]
    assert [entry["anchor_id"] for entry in by_object] == ["api-TargetCatalog"]
    assert find_doc_entries("wayfinder.planning.get_visibility")[0]["qualified_name"] == (
        "ObservationPlanning.get_visibility"
    )


def test_find_doc_entries_suggests_close_names() -> None:
    """A misspelled name gives close names marked as suggestions."""
    from backend.services.infrastructure.api_doc_generator import find_doc_entries

    suggestions = find_doc_entries("delete_imagse")

    assert suggestions
    assert all(entry["suggestion"] for entry in suggestions)
    assert "TargetCatalog.delete_images" in [entry["qualified_name"] for entry in suggestions]
    assert find_doc_entries("zzzzqqqq") == []
    assert find_doc_entries("") == []
