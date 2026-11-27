"""Purpose: Build a method's input models from the JSON a client sends.

Description: Some public methods take a pydantic model as an argument, or a
list of them: a horizon zone, a dither setting, an exposure request. A client
can send only JSON, so the tool offers that model's own JSON schema for the
argument, and this module checks the client's value against the model and
builds it before the method is called. A value that does not fit the model
is refused with an `InvalidArgumentError` that lists what is wrong.

A parameter counts as an input model when its type hint, without `None`, is
one model class, a model or a plain dictionary (``X | dict[str, Any]``), or a
list of either. Any other hint is left to the method.

The models' JSON schemas name their nested models through ``$ref`` links to
``#/$defs/...``. The links point at the top of the tool's schema, so
`model_schema` hands back the nested definitions for the reflection to put
there.
"""

import types
import typing
from dataclasses import dataclass
from typing import Any, Union

from pydantic import BaseModel, TypeAdapter, ValidationError

from astrometricslib.exceptions import InvalidArgumentError


@dataclass(frozen=True)
class ModelParameter:
    """A parameter that takes an input model, as the reflection found it.

    Attributes
    ----------
    model : `type` [`BaseModel`]
        The model class.
    many : `bool`
        `True` when the parameter takes a list of models.
    """

    model: type[BaseModel]
    many: bool = False


def _members(hint: Any) -> list[Any]:
    """List the members of a union hint, without `None`.

    Parameters
    ----------
    hint : `Any`
        A type hint.

    Returns
    -------
    members : `list`
        The union's members, or the hint alone.
    """
    if typing.get_origin(hint) in (Union, types.UnionType):
        return [member for member in typing.get_args(hint) if member is not type(None)]
    return [hint]


def _single_model(hint: Any) -> type[BaseModel] | None:
    """Give the model of a hint that is one model, maybe beside a dictionary.

    Parameters
    ----------
    hint : `Any`
        A type hint, or a member of one.

    Returns
    -------
    model : `type` [`BaseModel`] or `None`
        The model class, or `None` when the hint is anything else.
    """
    members = [
        member for member in _members(hint) if member is not dict and typing.get_origin(member) is not dict
    ]
    if len(members) == 1 and isinstance(members[0], type) and issubclass(members[0], BaseModel):
        if typing.get_args(members[0]) or "[" in members[0].__name__:
            # A generic model such as Page[FrameRecord] is a result.
            return None
        return members[0]
    return None


def model_parameter_of(hint: Any) -> ModelParameter | None:
    """Tell whether a parameter's type hint is an input model.

    Parameters
    ----------
    hint : `Any`
        The parameter's type hint.

    Returns
    -------
    parameter : `ModelParameter` or `None`
        The model and whether a list is taken, or `None`.
    """
    model = _single_model(hint)
    if model is not None:
        return ModelParameter(model)
    members = _members(hint)
    if len(members) == 1 and typing.get_origin(members[0]) is list and typing.get_args(members[0]):
        inner = _single_model(typing.get_args(members[0])[0])
        if inner is not None:
            return ModelParameter(inner, many=True)
    return None


def model_schema(parameter: ModelParameter, description: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Give the JSON schema a client sees for an input model parameter.

    Parameters
    ----------
    parameter : `ModelParameter`
        What the parameter takes.
    description : `str`
        The parameter's description from the docstring.

    Returns
    -------
    schema : `dict` [`str`, `Any`]
        The model's JSON schema (or an array of it), with the description.
    definitions : `dict` [`str`, `Any`]
        The nested model definitions its ``$ref`` links name. They belong
        under ``$defs`` at the top of the tool's schema.
    """
    full = parameter.model.model_json_schema()
    definitions = full.pop("$defs", {})
    if parameter.many:
        return {"type": "array", "items": full, "description": description}, definitions
    return {**full, "description": description}, definitions


def build_model_argument(parameter: ModelParameter, name: str, value: Any) -> Any:
    """Build the model, or list of models, from what a client sent.

    Parameters
    ----------
    parameter : `ModelParameter`
        What the parameter takes.
    name : `str`
        The parameter's name, for the message.
    value : `Any`
        The client's JSON value. `None` and values that already are models
        pass through.

    Returns
    -------
    argument : `Any`
        The model, a list of models, or the value unchanged.

    Raises
    ------
    InvalidArgumentError
        If the value does not fit the model.
    """
    if value is None:
        return None
    adapter = TypeAdapter(list[parameter.model] if parameter.many else parameter.model)
    try:
        return adapter.validate_python(value)
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in issue['loc']) or name}: {issue['msg']}"
            for issue in error.errors()[:10]
        ]
        raise InvalidArgumentError(
            f"{name} is not a valid {parameter.model.__name__}: " + "; ".join(problems),
            details={"argument": name, "problems": problems},
        ) from error
