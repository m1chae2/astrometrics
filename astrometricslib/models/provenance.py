"""Data structures for the IVOA Provenance Data Model (PROV-DM).

Every pipeline run today records its own rich, domain-specific quality
report (see `quality_summary.py`), but nothing links that report -- or a
star's own `PhotometryResult`/`SpectroscopyResult` -- back to *which run,
at what software version, consuming which inputs* produced it. The IVOA
Provenance Data Model (a 2020 formal recommendation built on the general
W3C PROV standard) exists to answer exactly that question, and is
implemented here faithfully rather than as a smaller ad hoc scheme.

Three core classes -- `Entity` (a data product), `Activity` (a pipeline
run), `Agent` (the software that ran it) -- plus the relations between
them (`Used`, `WasGeneratedBy`, `WasAssociatedWith`, `WasAttributedTo`)
and the Description/template layer that documents what a *kind* of
activity or entity looks like in general (`ActivityDescription`,
`EntityDescription`, `UsageDescription`, `GenerationDescription`) and how
an activity was configured (`Parameter`, `ParameterDescription`,
`ConfigFile`, `ConfigFileDescription`, `WasConfiguredBy`).

This module only describes the shape of the data. The code that records
and queries it lives in `astrometricslib.drivers.provenance_store`, and
the single call site every pipeline goes through is
`astrometricslib.pipelines.shared.provenance_recording`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Activity",
    "ActivityDescription",
    "Agent",
    "AgentType",
    "Collection",
    "ConfigFile",
    "ConfigFileDescription",
    "DatasetDescription",
    "DatasetEntity",
    "Entity",
    "EntityDescription",
    "GenerationDescription",
    "Parameter",
    "ParameterDescription",
    "UsageDescription",
    "Used",
    "ValueDescription",
    "ValueEntity",
    "WasAssociatedWith",
    "WasAttributedTo",
    "WasConfiguredBy",
    "WasGeneratedBy",
]


class AgentType(StrEnum):
    """Who or what an `Agent` represents.

    Every `Agent` this codebase creates today is a `SOFTWARE_AGENT` (one
    pipeline at one version) -- the other two values exist because the
    spec defines them, for a future person/organization attribution
    (for example, a manually ingested frame attributed to whoever added
    it).
    """

    PERSON = "Person"
    ORGANIZATION = "Organization"
    SOFTWARE_AGENT = "SoftwareAgent"


class Agent(BaseModel):
    """Something responsible for an activity, or for an entity existing.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str`
        Display name -- for a `SoftwareAgent`, the pipeline name and
        version it represents (for example ``"astrometricslib.photometry
        v1.2.0"``).
    type : `AgentType`
        What kind of agent this is. Defaults to `SOFTWARE_AGENT`, since
        that is the only kind this codebase creates today.
    comment : `str` or `None`
        Free-text note.
    email, affiliation, phone, address, url : `str` or `None`
        Contact details for a `PERSON`/`ORGANIZATION` agent. Always
        `None` for a `SOFTWARE_AGENT`. Kept because the spec defines
        them, not because anything sets them yet.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    type: AgentType = Field(default=AgentType.SOFTWARE_AGENT)
    comment: str | None = None
    email: str | None = None
    affiliation: str | None = None
    phone: str | None = None
    address: str | None = None
    url: str | None = None


class Entity(BaseModel):
    """A data product in a given state -- an image, a result, a value.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str` or `None`
        Display name.
    location : `str` or `None`
        A path, a URL, coordinates, or the name of a place.
    generated_at_time : `datetime` or `None`
        When this entity came into existence.
    invalidated_at_time : `datetime` or `None`
        When this entity stopped being available/current.
    comment : `str` or `None`
        Free-text note.
    entity_description : `str` or `None`
        Id of the `EntityDescription` (or subclass) describing what
        *kind* of entity this is, if one is registered.
    used_entity : `list` [`str`]
        Ids of the entities this one was derived from (the spec's
        ``wasDerivedFrom`` relation, modeled as a direct attribute
        rather than its own relation class).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str | None = Field(default=None)
    location: str | None = Field(default=None)
    generated_at_time: datetime | None = Field(default=None, alias="generatedAtTime")
    invalidated_at_time: datetime | None = Field(default=None, alias="invalidatedAtTime")
    comment: str | None = Field(default=None)
    entity_description: str | None = Field(default=None, alias="entityDescription")
    used_entity: list[str] = Field(default_factory=list, alias="usedEntity")


class Collection(Entity):
    """An `Entity` that groups other entities together.

    Attributes
    ----------
    entity : `list` [`str`]
        Ids of the member entities.
    """

    entity: list[str] = Field(default_factory=list)


class DatasetEntity(Entity):
    """An `Entity` that is a data file, such as a stacked FITS image."""


class ValueEntity(Entity):
    """An `Entity` that is a single scalar value rather than a file.

    Attributes
    ----------
    value : `str`
        The value, stored as text.
    """

    value: str


class Used(BaseModel):
    """Binds an `Activity` to one `Entity` it consumed as input.

    Attributes
    ----------
    role : `str` or `None`
        The entity's function with respect to the activity, for example
        ``"input_image"``.
    time : `datetime` or `None`
        When the activity started using this entity.
    entity : `str`
        Id of the entity consumed.
    usage_description : `str` or `None`
        Id of the `UsageDescription` this usage matches, if one is
        registered.
    """

    model_config = ConfigDict(populate_by_name=True)

    role: str | None = Field(default=None)
    time: datetime | None = Field(default=None)
    entity: str
    usage_description: str | None = Field(default=None, alias="usageDescription")


class WasGeneratedBy(BaseModel):
    """Binds an `Entity` to the one `Activity` that produced it.

    Cardinality is 0..1 per the spec -- an entity has at most one
    generating activity -- enforced in storage by
    `astrometricslib.drivers.provenance_store` via a primary key on the
    entity id, not by this model alone.

    Attributes
    ----------
    role : `str` or `None`
        The entity's function in the context of that activity.
    activity : `str`
        Id of the generating activity.
    generation_description : `str` or `None`
        Id of the `GenerationDescription` this generation matches, if
        one is registered.
    """

    model_config = ConfigDict(populate_by_name=True)

    role: str | None = Field(default=None)
    activity: str
    generation_description: str | None = Field(default=None, alias="generationDescription")


class WasAssociatedWith(BaseModel):
    """Denotes that an `Agent` is responsible for an `Activity`.

    Attributes
    ----------
    role : `str` or `None`
        The agent's function with respect to the activity.
    agent : `str`
        Id of the responsible agent.
    """

    model_config = ConfigDict(populate_by_name=True)

    role: str | None = Field(default=None)
    agent: str


class WasAttributedTo(BaseModel):
    """Links an `Entity` to the `Agent` responsible for it.

    Used for entities with no generating `Activity` at all -- for
    example a stacked image added by hand rather than produced by a
    pipeline run.

    Attributes
    ----------
    role : `str` or `None`
        The agent's function with respect to the entity.
    agent : `str`
        Id of the responsible agent.
    """

    model_config = ConfigDict(populate_by_name=True)

    role: str | None = Field(default=None)
    agent: str


class UsageDescription(BaseModel):
    """Describes, in general, one kind of input an `ActivityDescription` needs.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    role : `str`
        The input's function, for example ``"input_frames"``.
    description : `str` or `None`
        Explanatory text.
    type : `str` or `None`
        A short category label.
    multiplicity : `str` or `None`
        How many entities are expected, for example ``"1..*"``.
    entity_description : `list` [`str`]
        Ids of the `EntityDescription` rows this usage can be filled by.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    role: str
    description: str | None = Field(default=None)
    type: str | None = Field(default=None)
    multiplicity: str | None = Field(default=None)
    entity_description: list[str] = Field(default_factory=list, alias="entityDescription")


class GenerationDescription(BaseModel):
    """Describes, in general, one kind of output an activity produces.

    Same shape as `UsageDescription`, for outputs instead of inputs.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    role : `str`
        The output's function, for example ``"light_curve"``.
    description : `str` or `None`
        Explanatory text.
    type : `str` or `None`
        A short category label.
    multiplicity : `str` or `None`
        How many entities this activity type is expected to produce,
        for example ``"0..*"`` (photometry: one light curve per star).
    entity_description : `list` [`str`]
        Ids of the `EntityDescription` rows this generation produces.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    role: str
    description: str | None = Field(default=None)
    type: str | None = Field(default=None)
    multiplicity: str | None = Field(default=None)
    entity_description: list[str] = Field(default_factory=list, alias="entityDescription")


class ParameterDescription(BaseModel):
    """Describes, in general, one named setting a pipeline can be run with.

    Built lazily: the first time a parameter name is seen, a minimal row
    (`name`, `value_type`) is recorded automatically. The richer fields
    below start `None` and can be filled in by hand later without any
    code change.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str`
        The parameter's name, as it appears in `resolved_parameters`.
    value_type : `str`
        The Python type observed for this parameter's value --
        ``"int"``, ``"float"``, ``"str"``, or ``"bool"``.
    unit, ucd, utype : `str` or `None`
        VO unit string, UCD identifier, and role in an external VO
        model, when known.
    min, max, default : `str` or `None`
        Valid range and default, when known.
    options : `list` [`str`] or `None`
        Allowed values, when the parameter is an enumeration.
    description : `str` or `None`
        Explanatory text.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    value_type: str = Field(alias="valueType")
    unit: str | None = Field(default=None)
    ucd: str | None = Field(default=None)
    utype: str | None = Field(default=None)
    min: str | None = Field(default=None)
    max: str | None = Field(default=None)
    default: str | None = Field(default=None)
    options: list[str] | None = Field(default=None)
    description: str | None = Field(default=None)


class Parameter(BaseModel):
    """One named setting an `Activity` was actually run with.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str`
        The parameter's name.
    value : `str`
        The value actually used, stored as text.
    parameter_description : `str` or `None`
        Id of the `ParameterDescription` this value's name matches, if
        one is registered.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    value: str
    parameter_description: str | None = Field(default=None, alias="parameterDescription")


class ConfigFileDescription(BaseModel):
    """Describes, in general, one kind of external configuration file.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str`
        Display name, for example ``"astrometrics.config.toml"``.
    description : `str` or `None`
        Explanatory text.
    docurl : `str` or `None`
        A link to documentation, when one exists.
    type : `str` or `None`
        A short category label, for example ``"TOML"``.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = Field(default=None)
    docurl: str | None = Field(default=None)
    type: str | None = Field(default=None)


class ConfigFile(BaseModel):
    """One actual configuration file an `Activity` was configured by.

    Content-addressed: the same file content always produces the same
    id, so an unedited file is never recorded twice, while an actual
    edit produces a new, distinct row -- preserving history rather than
    overwriting it.

    Attributes
    ----------
    id : `str`
        Unique identifier, derived from the file's content.
    name : `str`
        The file's own name.
    location : `str`
        Where the file was read from.
    comment : `str` or `None`
        Free-text note.
    config_file_description : `str` or `None`
        Id of the `ConfigFileDescription` this file matches, if one is
        registered.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    location: str
    comment: str | None = Field(default=None)
    config_file_description: str | None = Field(default=None, alias="configFileDescription")


class WasConfiguredBy(BaseModel):
    """Binds an `Activity` to one configuration artefact it used.

    Two of these are recorded per activity -- one for its resolved
    `Parameter` values, one for the `ConfigFile` it read them from --
    each a legitimate, non-overlapping use of this same relation class.

    Attributes
    ----------
    activity : `str`
        Id of the configured activity.
    artefact_type : {"Parameter", "ConfigFile"}
        Which kind of artefact this record describes.
    parameters : `list` [`str`]
        Ids of the `Parameter` rows, when `artefact_type` is
        ``"Parameter"``.
    config_file : `str` or `None`
        Id of the `ConfigFile`, when `artefact_type` is
        ``"ConfigFile"``.
    """

    model_config = ConfigDict(populate_by_name=True)

    activity: str
    artefact_type: Literal["Parameter", "ConfigFile"] = Field(alias="artefactType")
    parameters: list[str] = Field(default_factory=list)
    config_file: str | None = Field(default=None, alias="configFile")


class ActivityDescription(BaseModel):
    """Describes, in general, what one kind of pipeline run looks like.

    One row per (pipeline name, pipeline version) pair -- not per run.
    Every actual `Activity` of that pipeline and version points back at
    the same description.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str`
        The pipeline's name, for example ``"photometry"``.
    version : `str` or `None`
        The pipeline version this description applies to.
    type : `str` or `None`
        A short category label, for example ``"ImageAnalysis"``.
    subtype : `str` or `None`
        A more specific category, for example ``"TimeSeriesPhotometry"``.
    description : `str` or `None`
        A one-sentence explanation of what this pipeline does.
    docurl : `str` or `None`
        A link to documentation, when one exists.
    usage_description, generation_description, parameter_description,
    config_file_description : `list` [`str`]
        Ids of this pipeline's registered inputs, outputs, parameters,
        and configuration files.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    version: str | None = Field(default=None)
    type: str | None = Field(default=None)
    subtype: str | None = Field(default=None)
    description: str | None = Field(default=None)
    docurl: str | None = Field(default=None)
    usage_description: list[str] = Field(default_factory=list, alias="usageDescription")
    generation_description: list[str] = Field(default_factory=list, alias="generationDescription")
    parameter_description: list[str] = Field(default_factory=list, alias="parameterDescription")
    config_file_description: list[str] = Field(default_factory=list, alias="configFileDescription")


class EntityDescription(BaseModel):
    """Describes, in general, what one kind of entity looks like.

    Attributes
    ----------
    id : `str`
        Unique identifier.
    name : `str`
        Display name, for example ``"PhotometryResults"``.
    description : `str` or `None`
        Explanatory text.
    docurl : `str` or `None`
        A link to documentation, when one exists.
    type : `str` or `None`
        A short category label.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = Field(default=None)
    docurl: str | None = Field(default=None)
    type: str | None = Field(default=None)


class DatasetDescription(EntityDescription):
    """An `EntityDescription` for a kind of data file.

    Attributes
    ----------
    content_type : `str`
        The file's MIME type, for example ``"image/fits"``.
    """

    content_type: str = Field(alias="contentType")


class ValueDescription(EntityDescription):
    """An `EntityDescription` for a kind of single scalar value.

    Attributes
    ----------
    value_type : `str` or `None`
        The value's data type.
    unit, ucd, utype : `str` or `None`
        VO unit string, UCD identifier, and role in an external VO
        model, when known.
    """

    value_type: str | None = Field(default=None, alias="valueType")
    unit: str | None = Field(default=None)
    ucd: str | None = Field(default=None)
    utype: str | None = Field(default=None)


class Activity(BaseModel):
    """One pipeline run.

    `id` is always the same id the existing job-tracking system
    (`astrometricslib.drivers.job_logging.registered_job`) already
    assigned that run -- there is no separate id scheme.

    Attributes
    ----------
    id : `str`
        Unique identifier; equal to the run's `job_id`.
    name : `str` or `None`
        The pipeline's name, for example ``"photometry"``.
    start_time, end_time : `datetime` or `None`
        When the run started and finished.
    comment : `str` or `None`
        Free-text note.
    activity_description : `str` or `None`
        Id of the `ActivityDescription` this run matches, if one is
        registered.
    informant : `list` [`str`]
        Ids of earlier activities this one was informed by -- how a
        multi-stage run (stacking, then astrometry, then photometry,
        then spectroscopy) is chained together, since the spec has no
        separate "workflow" class.
    used : `list` [`Used`]
        The entities this run consumed as input.
    was_associated_with : `list` [`WasAssociatedWith`]
        The agents responsible for this run.
    was_configured_by : `list` [`WasConfiguredBy`]
        The configuration artefacts this run used.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str | None = Field(default=None)
    start_time: datetime | None = Field(default=None, alias="startTime")
    end_time: datetime | None = Field(default=None, alias="endTime")
    comment: str | None = Field(default=None)
    activity_description: str | None = Field(default=None, alias="activityDescription")
    informant: list[str] = Field(default_factory=list)
    used: list[Used] = Field(default_factory=list)
    was_associated_with: list[WasAssociatedWith] = Field(default_factory=list, alias="wasAssociatedWith")
    was_configured_by: list[WasConfiguredBy] = Field(default_factory=list, alias="wasConfiguredBy")
