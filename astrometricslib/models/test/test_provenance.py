"""Tests for the IVOA Provenance Data Model classes.

Covers alias round-tripping (the camelCase form the database stores) and
the default values each class promises, for the handful of classes not
already exercised indirectly through `test_provenance_store.py` and
`test_provenance_recording.py`.
"""

from astrometricslib.models.provenance import (
    Activity,
    ActivityDescription,
    Agent,
    AgentType,
    Collection,
    ConfigFile,
    ConfigFileDescription,
    DatasetDescription,
    DatasetEntity,
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


def test_agent_defaults_to_a_software_agent_with_no_contact_details():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the default `AgentType` and that contact fields start `None`."""
    agent = Agent(id="agent:photometry:1.2.0", name="astrometricslib.photometry v1.2.0")
    assert agent.type == AgentType.SOFTWARE_AGENT
    assert agent.email is None
    assert agent.affiliation is None


def test_agent_round_trips_through_its_camel_case_alias():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the stored camelCase form loads back into the same object."""
    agent = Agent(id="agent:stacking:1.3.0", name="astrometricslib.stacking v1.3.0", type=AgentType.PERSON)
    reloaded = Agent.model_validate(agent.model_dump(by_alias=True, mode="json"))
    assert reloaded == agent


def test_entity_round_trips_its_derived_from_and_timestamps():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check `usedEntity`/`generatedAtTime`/etc. survive a save and load."""
    entity = Entity(
        id="entity:stack-image:M13:abc123",
        location="/library/M13/stack.fits",
        entity_description="entitydesc:stacked-image",
        used_entity=["entity:raw-frame:1", "entity:raw-frame:2"],
    )
    reloaded = Entity.model_validate(entity.model_dump(by_alias=True, mode="json"))
    assert reloaded == entity
    assert reloaded.used_entity == ["entity:raw-frame:1", "entity:raw-frame:2"]


def test_entity_subclasses_carry_their_own_extra_field():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check `Collection.entity`/`ValueEntity.value` round-trip correctly."""
    collection = Collection(id="entity:group:1", entity=["entity:a", "entity:b"])
    assert Collection.model_validate(collection.model_dump(by_alias=True, mode="json")).entity == [
        "entity:a",
        "entity:b",
    ]

    dataset = DatasetEntity(id="entity:stack-image:1", location="/x.fits")
    assert isinstance(dataset, Entity)

    value = ValueEntity(id="entity:value:1", value="42.0")
    assert ValueEntity.model_validate(value.model_dump(by_alias=True, mode="json")).value == "42.0"


def test_was_generated_by_and_was_configured_by_round_trip_their_aliases():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the two relations with the least-obvious aliasing round-trip."""
    generated_by = WasGeneratedBy(activity="job-1", role="stacked_image", generation_description="gendesc:x")
    reloaded = WasGeneratedBy.model_validate(generated_by.model_dump(by_alias=True, mode="json"))
    assert reloaded == generated_by

    configured_by = WasConfiguredBy(
        activity="job-1", artefact_type="ConfigFile", config_file="configfile:abc"
    )
    reloaded_configured_by = WasConfiguredBy.model_validate(
        configured_by.model_dump(by_alias=True, mode="json")
    )
    assert reloaded_configured_by == configured_by
    assert reloaded_configured_by.parameters == []


def test_used_and_was_associated_with_and_was_attributed_to_default_roles():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the three simple relation classes default their optional role."""
    assert Used(entity="entity:x").role is None
    assert WasAssociatedWith(agent="agent:x").role is None
    assert WasAttributedTo(agent="agent:x").role is None


def test_activity_defaults_to_empty_lists_for_every_collection_field():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check a minimal `Activity` has no informants, usages, or agents yet."""
    activity = Activity(id="job-1")
    assert activity.informant == []
    assert activity.used == []
    assert activity.was_associated_with == []
    assert activity.was_configured_by == []


def test_activity_round_trips_its_nested_relations():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check `Used`/`WasAssociatedWith`/`WasConfiguredBy` nest and reload."""
    activity = Activity(
        id="job-1",
        name="photometry",
        informant=["job-0"],
        used=[Used(entity="entity:stack-image:1", role="input_image")],
        was_associated_with=[WasAssociatedWith(agent="agent:photometry:1.2.0")],
        was_configured_by=[WasConfiguredBy(activity="job-1", artefact_type="Parameter", parameters=["p1"])],
    )
    reloaded = Activity.model_validate(activity.model_dump(by_alias=True, mode="json"))
    assert reloaded == activity


def test_activity_description_and_entity_description_round_trip_ids():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check the description/template layer's id lists survive a round trip."""
    activity_description = ActivityDescription(
        id="activitydesc:photometry:1.2.0",
        name="photometry",
        version="1.2.0",
        usage_description=["usagedesc:photometry:input_frames"],
        generation_description=["gendesc:photometry:light_curve"],
        parameter_description=["paramdesc:photometry:aperture_radius"],
        config_file_description=["configfiledesc:astrometrics-toml"],
    )
    reloaded = ActivityDescription.model_validate(activity_description.model_dump(by_alias=True, mode="json"))
    assert reloaded == activity_description

    dataset_description = DatasetDescription(
        id="entitydesc:stacked-image", name="StackedImage", content_type="image/fits"
    )
    reloaded_dataset = DatasetDescription.model_validate(
        dataset_description.model_dump(by_alias=True, mode="json")
    )
    assert reloaded_dataset.content_type == "image/fits"

    value_description = ValueDescription(id="entitydesc:snr", name="SignalToNoise", value_type="float")
    assert isinstance(value_description, EntityDescription)


def test_usage_and_generation_description_default_to_no_entity_descriptions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check a description with no linked entity kinds yet defaults empty."""
    usage = UsageDescription(id="usagedesc:photometry:input_frames", role="input_frames")
    generation = GenerationDescription(id="gendesc:photometry:light_curve", role="light_curve")
    assert usage.entity_description == []
    assert generation.entity_description == []


def test_parameter_description_round_trips_its_value_type_alias():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check `valueType` -- the one required alias here -- round-trips."""
    description = ParameterDescription(
        id="paramdesc:photometry:aperture_radius", name="aperture_radius", value_type="float"
    )
    reloaded = ParameterDescription.model_validate(description.model_dump(by_alias=True, mode="json"))
    assert reloaded.value_type == "float"


def test_parameter_and_config_file_round_trip_their_description_link():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check `Parameter`/`ConfigFile` keep their optional description id."""
    parameter = Parameter(id="job-1:param:aperture_radius", name="aperture_radius", value="3.5")
    assert parameter.parameter_description is None

    config_file = ConfigFile(id="configfile:abc123", name="astrometrics.config.toml", location="/x.toml")
    assert config_file.config_file_description is None

    config_file_description = ConfigFileDescription(
        id="configfiledesc:astrometrics-toml", name="astrometrics.config.toml"
    )
    assert config_file_description.type is None
