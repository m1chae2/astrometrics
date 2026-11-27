"""Purpose: Tests for the reply shaper and its table of heavy lists.

Description: Checks that every row of `HEAVY_LISTS` names a model some public
method returns and a list field of it, that a reply too long for the budget
has its heavy lists cut with a note that says what was left out and how to
ask for it, and that a reply that fits is sent as it is.
"""

import json
import typing
from typing import Any

from pydantic import BaseModel

from astrometricslib import Astrometrics, FrameRecord, Target
from astrometricslib.arguments import Page
from mcp_servers.astrometrics_core import definition as astrometrics_definition
from mcp_servers.common.reply_budget import HEAVY_LISTS, reply_budget, reply_characters, shape_reply
from mcp_servers.common.tool_registry import MAXIMUM_REPLY_BYTES, result_content
from mcp_servers.wayfinding_core import definition as wayfinding_definition
from wayfindinglib import Wayfinder


def _models_in(hint: Any, found: dict[str, type[BaseModel]]) -> None:
    """Collect every model class a type hint can hold, by class name.

    Parameters
    ----------
    hint : `Any`
        A type hint.
    found : `dict` [`str`, `type` [`BaseModel`]]
        Filled with the models, recursively through their fields.
    """
    for member in typing.get_args(hint):
        _models_in(member, found)
    if isinstance(hint, type) and issubclass(hint, BaseModel):
        origin = hint.__pydantic_generic_metadata__.get("origin") or hint
        if origin.__name__ in found:
            return
        found[origin.__name__] = origin
        for model_field in hint.model_fields.values():
            _models_in(model_field.annotation, found)


def _returned_models() -> dict[str, type[BaseModel]]:
    """List the models the reflected public methods of both libraries return.

    Returns
    -------
    models : `dict` [`str`, `type` [`BaseModel`]]
        Class name -> class, with every nested model, and `Page`.
    """
    found: dict[str, type[BaseModel]] = {"Page": Page}
    wayfinder = Wayfinder()
    for root, prefixes in (
        (wayfinder.astrometrics, astrometrics_definition.BRANCH_PREFIXES),
        (wayfinder, wayfinding_definition.BRANCH_PREFIXES),
    ):
        for attribute in prefixes:
            api = root
            for part in attribute.split("."):
                api = getattr(api, part)
            for name in dir(api):
                method = getattr(api, name)
                if name.startswith("_") or not callable(method):
                    continue
                try:
                    _models_in(typing.get_type_hints(method).get("return"), found)
                except NameError, TypeError:
                    continue
    assert isinstance(wayfinder.astrometrics, Astrometrics)
    return found


def test_every_heavy_list_is_a_list_field_of_a_returned_model() -> None:
    """A row that names a model or field no method returns fails."""
    models = _returned_models()
    for entry in HEAVY_LISTS:
        assert entry.model in models, f"{entry.model} is returned by no public method"
        model_fields = models[entry.model].model_fields
        for field_name in entry.fields:
            assert field_name in model_fields, f"{entry.model} has no field {field_name}"
            annotation = str(model_fields[field_name].annotation)
            assert "list[" in annotation or "tuple[" in annotation, (
                f"{entry.model}.{field_name} is not a list"
            )
        assert entry.keep >= 0 and entry.how_to_ask


def _big_target(frame_count: int) -> Target:
    """Build a target with many frames.

    Returns
    -------
    target : `Target`
        A target whose frames make its JSON far longer than the budget.
    """
    return Target(
        id="M 31", frames=[FrameRecord(path=f"/frames/M31/light_{n:05d}.fits") for n in range(frame_count)]
    )


def test_a_reply_that_fits_is_sent_as_it_is() -> None:
    """Nothing is cut and no note is written."""
    target = _big_target(3)
    shaped = shape_reply(target)
    assert shaped.reply is target
    assert shaped.notes == ()


def test_a_long_frame_list_is_cut_with_a_note() -> None:
    """The frames are cut to fit; the note counts them and says how to ask."""
    shaped = shape_reply(_big_target(3000))
    assert 0 < len(shaped.reply.frames) <= 20
    assert reply_characters(shaped.reply) <= reply_budget()
    assert len(shaped.notes) == 1
    assert "of 3,000 frames" in shaped.notes[0]
    assert "target_select_frames" in shaped.notes[0]


def test_a_page_of_targets_loses_frames_before_targets() -> None:
    """Heavy lists inside the items are cut in the same rounds as the page."""
    page = Page.of([_big_target(400) for _ in range(30)])
    shaped = shape_reply(page)
    assert reply_characters(shaped.reply) <= reply_budget()
    assert any("frames" in note for note in shaped.notes)
    assert shaped.reply.total == 30


def test_the_registry_sends_the_note_after_valid_json() -> None:
    """The reply is valid JSON within the limit, then the note."""
    content = result_content(_big_target(3000))
    assert len(content) == 2
    assert len(content[0].text) <= MAXIMUM_REPLY_BYTES
    assert json.loads(content[0].text)["id"] == "M 31"
    assert content[1].text.startswith("Left out ")
