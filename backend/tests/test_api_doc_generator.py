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
