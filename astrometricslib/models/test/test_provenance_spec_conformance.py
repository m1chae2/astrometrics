"""Checks that `provenance.py` still matches the IVOA/W3C PROV-DM spec.

Ruff and pyrefly only check internal consistency -- fully typed, docstrings
that match a real signature -- neither verifies fidelity to the external
standard itself. This test is the one place that does: it pins each
PROV-DM class's field set against what was actually designed from the
2020-04-11 IVOA Recommendation, so a future edit that silently drifts
from the spec (an added/removed/renamed field) fails here instead of
going unnoticed. It also proves the `WasGeneratedBy` 0..1 cardinality
rule holds in the store, not just in the schema's primary key comment.
"""

from pathlib import Path

from astrometricslib.drivers.provenance_store import ProvenanceStore
from astrometricslib.models.provenance import (
    Activity,
    ActivityDescription,
    Agent,
    Collection,
    ConfigFile,
    ConfigFileDescription,
    DatasetDescription,
    Entity,
    EntityDescription,
    GenerationDescription,
    Parameter,
    ParameterDescription,
    UsageDescription,
    Used,
    ValueDescription,
    ValueEntity,
    WasAssociatedWith,
    WasAttributedTo,
    WasConfiguredBy,
    WasGeneratedBy,
)


def field_names(model_class: type) -> set[str]:
    """List a pydantic model's own field names.

    Returns
    -------
    names : `set` [`str`]
        The model's field names (Python attribute names, not aliases).
    """
    return set(model_class.model_fields.keys())


def test_agent_has_exactly_the_spec_s_core_and_optional_contact_fields() -> None:
    """Check `Agent` -- one of the three core PROV-DM classes -- is intact."""
    assert field_names(Agent) == {
        "id",
        "name",
        "type",
        "comment",
        "email",
        "affiliation",
        "phone",
        "address",
        "url",
    }


def test_entity_has_exactly_the_spec_s_core_fields_plus_derived_from() -> None:
    """Check `Entity` -- including `used_entity` for `wasDerivedFrom`."""
    assert field_names(Entity) == {
        "id",
        "name",
        "location",
        "generated_at_time",
        "invalidated_at_time",
        "comment",
        "entity_description",
        "used_entity",
    }
    assert field_names(Collection) == field_names(Entity) | {"entity"}
    assert field_names(ValueEntity) == field_names(Entity) | {"value"}


def test_activity_has_exactly_the_spec_s_core_fields_plus_informant() -> None:
    """Check `Activity` -- including `informant` for `wasInformedBy`.

    There is no `ActivityFlow`/`hadStep` class here on purpose: the real
    2020-04-11 IVOA Recommendation does not define one, confirmed against
    the actual document rather than a summary of it. Multi-stage chaining
    uses `informant` (the spec's own attribute for `wasInformedBy`).
    """
    assert field_names(Activity) == {
        "id",
        "name",
        "start_time",
        "end_time",
        "comment",
        "activity_description",
        "informant",
        "used",
        "was_associated_with",
        "was_configured_by",
    }


def test_the_four_core_relations_have_exactly_the_spec_s_fields() -> None:
    """Check `Used`/`WasGeneratedBy`/`WasAssociatedWith`/`WasAttributedTo`."""
    assert field_names(Used) == {"role", "time", "entity", "usage_description"}
    assert field_names(WasGeneratedBy) == {"role", "activity", "generation_description"}
    assert field_names(WasAssociatedWith) == {"role", "agent"}
    assert field_names(WasAttributedTo) == {"role", "agent"}


def test_the_description_template_layer_has_exactly_the_spec_s_fields() -> None:
    """Check `ActivityDescription`/`EntityDescription`/`Usage`/`Generation`."""
    assert field_names(ActivityDescription) == {
        "id",
        "name",
        "version",
        "type",
        "subtype",
        "description",
        "docurl",
        "usage_description",
        "generation_description",
        "parameter_description",
        "config_file_description",
    }
    assert field_names(EntityDescription) == {"id", "name", "description", "docurl", "type"}
    assert field_names(DatasetDescription) == field_names(EntityDescription) | {"content_type"}
    assert field_names(ValueDescription) == field_names(EntityDescription) | {
        "value_type",
        "unit",
        "ucd",
        "utype",
    }
    assert field_names(UsageDescription) == {
        "id",
        "role",
        "description",
        "type",
        "multiplicity",
        "entity_description",
    }
    assert field_names(GenerationDescription) == field_names(UsageDescription)


def test_the_configuration_package_has_exactly_the_spec_s_fields() -> None:
    """Check the parameter and config-file classes' fields."""
    assert field_names(Parameter) == {"id", "name", "value", "parameter_description"}
    assert field_names(ParameterDescription) == {
        "id",
        "name",
        "value_type",
        "unit",
        "ucd",
        "utype",
        "min",
        "max",
        "default",
        "options",
        "description",
    }
    assert field_names(ConfigFile) == {"id", "name", "location", "comment", "config_file_description"}
    assert field_names(ConfigFileDescription) == {"id", "name", "description", "docurl", "type"}
    assert field_names(WasConfiguredBy) == {"activity", "artefact_type", "parameters", "config_file"}


def test_was_generated_by_cardinality_holds_in_the_store_not_just_in_the_schema(tmp_path: Path) -> None:
    """Check an entity keeps at most one generating activity, in practice.

    The 0..1 cardinality rule is enforced by `entity_id` being
    `prov_was_generated_by`'s primary key -- this proves that holds by
    recording two generating activities for the same entity and
    confirming the second replaces the first, rather than both
    coexisting or the write raising a constraint error.
    """
    store = ProvenanceStore(str(tmp_path / "provenance.db"))
    store.record_entity(Entity(id="entity:x"))
    store.record_was_generated_by("entity:x", WasGeneratedBy(activity="job-1", role="first"))
    store.record_was_generated_by("entity:x", WasGeneratedBy(activity="job-2", role="second"))

    assert store.get_generated_entity_ids("job-1") == []
    assert store.get_generated_entity_ids("job-2") == ["entity:x"]
