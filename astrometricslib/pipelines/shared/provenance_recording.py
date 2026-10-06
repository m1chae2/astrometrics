"""The one call every pipeline makes to record its IVOA provenance.

`record_pipeline_run` is the single chokepoint `pipelines/pipeline_base.py`
and `pipelines/stacking/stage.py` call after building a run's quality
summary -- no other code should talk to `ProvenanceStore` directly. It
knows, for each of the five pipelines, what kind of thing it generically
consumes and produces (`_PIPELINE_DEFINITIONS`), and uses that to build
the run's `ActivityDescription`/`UsageDescription`/`GenerationDescription`
the first time that pipeline's version is seen.

Modeled on `applied_camera_profile.py`'s pattern of one small function
call sites use to fill in a summary, rather than each pipeline touching
storage itself.
"""

import hashlib
import logging
import sqlite3
from typing import Any

from astrometricslib.drivers.provenance_store import ProvenanceStore
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.provenance import (
    Activity,
    ActivityDescription,
    Agent,
    ConfigFile,
    Entity,
    GenerationDescription,
    Parameter,
    ParameterDescription,
    UsageDescription,
    Used,
    WasAssociatedWith,
    WasConfiguredBy,
    WasGeneratedBy,
)
from astrometricslib.models.quality_summary import PipelineQualitySummaryBase

logger = logging.getLogger(__name__)

# What each pipeline generically consumes and produces, hand-authored
# from the architecture each pipeline actually follows -- used to build
# that pipeline's ActivityDescription/UsageDescription/GenerationDescription
# the first time its version is seen. Ids for the description rows are
# derived from this table's own keys (see `_ensure_activity_description`),
# not stored here.
_PIPELINE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "stacking": {
        "type": "ImageProcessing",
        "subtype": "FrameStacking",
        "description": "Combines multiple raw frames of a target into one calibrated image.",
        "usages": [{"role": "input_frames", "multiplicity": "1..*", "entity_description": None}],
        "generations": [
            {
                "role": "stacked_image",
                "multiplicity": "0..1",
                "entity_description": "entitydesc:stacked-image",
            }
        ],
    },
    "astrometry": {
        "type": "ImageAnalysis",
        "subtype": "PlateSolving",
        "description": (
            "Detects point sources in an image and solves its World Coordinate "
            "System by matching detected stars against the SIMBAD/Gaia catalogs."
        ),
        "usages": [
            {"role": "input_image", "multiplicity": "1", "entity_description": "entitydesc:stacked-image"}
        ],
        "generations": [
            {
                "role": "catalog_match_results",
                "multiplicity": "0..*",
                "entity_description": "entitydesc:catalog-match-results",
            }
        ],
    },
    "photometry": {
        "type": "ImageAnalysis",
        "subtype": "TimeSeriesPhotometry",
        "description": (
            "Tracks the brightness of stars across many frames to build light curves and detect variability."
        ),
        "usages": [{"role": "input_frames", "multiplicity": "1..*", "entity_description": None}],
        "generations": [
            {
                "role": "light_curve",
                "multiplicity": "0..*",
                "entity_description": "entitydesc:photometry-results",
            }
        ],
    },
    "spectroscopy": {
        "type": "ImageAnalysis",
        "subtype": "SlitlessSpectroscopy",
        "description": "Extracts and classifies each star's light spectrum from a spectroscopy image.",
        "usages": [
            {"role": "input_image", "multiplicity": "1..*", "entity_description": "entitydesc:stacked-image"}
        ],
        "generations": [
            {
                "role": "spectrum_result",
                "multiplicity": "0..*",
                "entity_description": "entitydesc:spectroscopy-results",
            }
        ],
    },
    "asteroid_detection": {
        "type": "ImageAnalysis",
        "subtype": "MovingObjectDetection",
        "description": "Searches a sequence of images for objects moving against the fixed background stars.",
        "usages": [
            {"role": "input_frames", "multiplicity": "1..*", "entity_description": None},
            {"role": "input_image", "multiplicity": "0..1", "entity_description": "entitydesc:stacked-image"},
        ],
        "generations": [
            {
                "role": "candidate_list",
                "multiplicity": "0..1",
                "entity_description": "entitydesc:asteroid-candidate-list",
            }
        ],
    },
}


# Which nested result on a StellarObject each pipeline's own run
# touches -- used by `stamp_generated_by_job_id` to know which field to
# mark with this run's job id. Astrometry's `catalog_match_quality` is
# `None` for a star with no catalog match; photometry's and
# spectroscopy's `PhotometryResult`/`SpectroscopyResult` are always real
# objects (default-constructed, never `None`), but the `None` check
# below covers both cases identically.
_STELLAR_OBJECT_RESULT_FIELD_BY_PIPELINE: dict[str, str] = {
    "astrometry": "catalog_match_quality",
    "photometry": "photometry",
    "spectroscopy": "spectroscopy",
}


def stamp_generated_by_job_id(pipeline_name: str, stellar_objects: list[Any], job_id: str | None) -> None:
    """Mark every star this run touched with the job that touched it.

    Stamps in place rather than threading `job_id` through each
    pipeline's internal construction/merge logic (`variability_analyzer.py`,
    `batch.py`, `star_recording.py`, `pipeline.py`, `star_identifier.py`) --
    `stellar_objects` already is "every star this run saved," so this is
    the one place that needs to know about job ids at all.

    Parameters
    ----------
    pipeline_name : `str`
        Which pipeline ran -- looked up in
        `_STELLAR_OBJECT_RESULT_FIELD_BY_PIPELINE` to find which nested
        result to stamp. Does nothing for a pipeline (for example
        ``"asteroid_detection"``) with no entry there.
    stellar_objects : `list`
        The stars this run touched (`Result.stellar_objects`).
    job_id : `str` or `None`
        The run's job id. Does nothing at all when `None`, matching
        `record_pipeline_run`'s own no-job behavior.
    """
    if job_id is None:
        return
    field_name = _STELLAR_OBJECT_RESULT_FIELD_BY_PIPELINE.get(pipeline_name)
    if field_name is None:
        return
    for stellar_object in stellar_objects:
        nested_result = getattr(stellar_object, field_name, None)
        if nested_result is not None:
            nested_result.generated_by_job_id = job_id


def stacked_image_entity_id(target_id: str, stacked_image_path: str) -> str:
    """Compute the deterministic provenance entity id for a stacked image.

    Keyed by the target and the stacked image's own path, not by the
    stacking run's job id, so any later pipeline (astrometry,
    spectroscopy, asteroid detection) can recompute the same id from
    `target.stacking.stacked_image` alone, without needing to have remembered
    which job produced it.

    Parameters
    ----------
    target_id : `str`
        The target the stacked image belongs to.
    stacked_image_path : `str`
        The stacked image's file path.

    Returns
    -------
    entity_id : `str`
        The stacked image's provenance entity id.
    """
    digest = hashlib.sha256(stacked_image_path.encode()).hexdigest()[:12]
    return f"entity:stack-image:{target_id}:{digest}"


def note_stacked_image_upstream(
    options: dict[str, Any], target_id: str, stacked_image_path: str | None, role: str
) -> None:
    """Record a stacked image as one run's upstream input, in place.

    A small shared helper for the astrometry/spectroscopy/asteroid-detection
    runners, each of which resolves its own input from a target's already
    stacked image before dispatching to `run_pipeline`. Does nothing when
    `stacked_image_path` is falsy, so a runner can call this unconditionally.

    Parameters
    ----------
    options : `dict`
        The `PipelineRequest.options` dict being built -- mutated in
        place with `upstream_entity_id` and a `used_entity_ids` entry.
    target_id : `str`
        The target the stacked image belongs to.
    stacked_image_path : `str` or `None`
        The stacked image's path, or `None`/empty if there is none.
    role : `str`
        This run's usage role for the image, for example
        ``"input_image"``.
    """
    if not stacked_image_path:
        return
    entity_id = stacked_image_entity_id(target_id, stacked_image_path)
    options["upstream_entity_id"] = entity_id
    options.setdefault("used_entity_ids", {})[entity_id] = role


def record_pipeline_run(
    *,
    summary: PipelineQualitySummaryBase,
    job_id: str | None,
    target_id: str,
    pipeline_name: str,
    pipeline_version: str,
    upstream_entity_id: str | None = None,
    informant_activity_ids: list[str] | None = None,
    used_entity_ids: dict[str, str] | None = None,
    generated_entities: dict[str, Entity] | None = None,
) -> None:
    """Record one pipeline run's IVOA provenance, and stamp it onto `summary`.

    Does nothing at all when `job_id` is `None` -- the same situation
    that already means no job row exists (a caller passed
    `register_job=False`), so no `Activity` is a consistent, not a new,
    gap. Never raises: a storage problem here must not turn an
    otherwise-successful pipeline run into a failure.

    Parameters
    ----------
    summary : `PipelineQualitySummaryBase`
        This run's quality summary. Its `resolved_parameters` are
        recorded as `Parameter`/`ParameterDescription` rows, and its
        `provenance_activity_id`/`upstream_entity_id` are set in place
        once recording succeeds.
    job_id : `str` or `None`
        The run's job id, from `registered_job`. Becomes the
        `Activity`'s id.
    target_id : `str`
        The target this run processed.
    pipeline_name : `str`
        Which of `_PIPELINE_DEFINITIONS` this run is, for example
        ``"photometry"``.
    pipeline_version : `str`
        The pipeline's current version (its own `*_PIPELINE_VERSION`
        constant).
    upstream_entity_id : `str`, optional
        The id of the upstream entity this run actually consumed (for
        example the stacked image astrometry solved), stamped onto
        `summary.upstream_entity_id`.
    informant_activity_ids : `list` [`str`], optional
        Ids of earlier activities this run was informed by, chaining a
        multi-stage run together.
    used_entity_ids : `dict` [`str`, `str`], optional
        Maps each input entity id this run consumed to its role.
    generated_entities : `dict` [`str`, `Entity`], optional
        Maps each output entity id this run produced to the entity
        itself. When omitted, one aggregate results entity is
        synthesized automatically from this pipeline's own
        `GenerationDescription` -- never one entity per star, since
        per-star traceability is the separate `generated_by_job_id`
        field on each star's own result.
    """
    if job_id is None:
        return

    used_entity_ids = used_entity_ids or {}
    informant_activity_ids = informant_activity_ids or []
    definition = _PIPELINE_DEFINITIONS.get(pipeline_name, {})

    try:
        # Imported here, not at module level, so a test's monkeypatch of
        # `config_loader.get_configuration` is honored -- the same
        # lazy-import convention `job_logging.registered_job` already
        # uses for the same reason.
        from astrometricslib.foundation.config import get_configuration

        config = get_configuration()
        store = ProvenanceStore(config.get_logs_db_path())

        activity_description_id = _ensure_activity_description(
            store, pipeline_name, pipeline_version, definition
        )

        agent_id = f"agent:{pipeline_name}:{pipeline_version}"
        store.record_agent(Agent(id=agent_id, name=f"astrometricslib.{pipeline_name} v{pipeline_version}"))

        store.record_activity(
            Activity(
                id=job_id,
                name=pipeline_name,
                activity_description=activity_description_id,
                informant=informant_activity_ids,
            ),
            target_id=target_id,
        )
        store.record_was_associated_with(job_id, WasAssociatedWith(agent=agent_id))

        parameter_ids = _record_parameters(store, job_id, pipeline_name, summary.resolved_parameters)
        if parameter_ids:
            store.record_was_configured_by(
                job_id, WasConfiguredBy(activity=job_id, artefact_type="Parameter", parameters=parameter_ids)
            )
        config_file_id = _record_config_file(store, config)
        if config_file_id is not None:
            store.record_was_configured_by(
                job_id,
                WasConfiguredBy(activity=job_id, artefact_type="ConfigFile", config_file=config_file_id),
            )

        usage_description_by_role = {
            usage["role"]: f"usagedesc:{pipeline_name}:{usage['role']}"
            for usage in definition.get("usages", [])
        }
        for entity_id, role in used_entity_ids.items():
            store.record_used(
                job_id,
                Used(entity=entity_id, role=role, usage_description=usage_description_by_role.get(role)),
            )

        _record_generated_entities(store, job_id, pipeline_name, definition, generated_entities)

        summary.provenance_activity_id = job_id
        summary.upstream_entity_id = upstream_entity_id
    except (AstrometricsError, sqlite3.Error, OSError, ValueError) as error:
        logger.warning("Could not record provenance for job %r (%s): %s", job_id, pipeline_name, error)


def _ensure_activity_description(
    store: ProvenanceStore, pipeline_name: str, pipeline_version: str, definition: dict[str, Any]
) -> str:
    """Register what this pipeline and version generically look like, if new.

    Returns
    -------
    activity_description_id : `str`
        The id every `Activity` of this pipeline and version should
        reference.
    """
    activity_description_id = f"activitydesc:{pipeline_name}:{pipeline_version}"
    usage_ids = []
    for usage in definition.get("usages", []):
        usage_id = f"usagedesc:{pipeline_name}:{usage['role']}"
        store.record_usage_description(
            activity_description_id,
            UsageDescription(
                id=usage_id,
                role=usage["role"],
                multiplicity=usage.get("multiplicity"),
                entity_description=[usage["entity_description"]] if usage.get("entity_description") else [],
            ),
        )
        usage_ids.append(usage_id)

    generation_ids = []
    for generation in definition.get("generations", []):
        generation_id = f"gendesc:{pipeline_name}:{generation['role']}"
        store.record_generation_description(
            activity_description_id,
            GenerationDescription(
                id=generation_id,
                role=generation["role"],
                multiplicity=generation.get("multiplicity"),
                entity_description=[generation["entity_description"]]
                if generation.get("entity_description")
                else [],
            ),
        )
        generation_ids.append(generation_id)

    store.ensure_activity_description(
        ActivityDescription(
            id=activity_description_id,
            name=pipeline_name,
            version=pipeline_version,
            type=definition.get("type"),
            subtype=definition.get("subtype"),
            description=definition.get("description"),
            usage_description=usage_ids,
            generation_description=generation_ids,
            config_file_description=["configfiledesc:astrometrics-toml"],
        )
    )
    return activity_description_id


def _record_generated_entities(
    store: ProvenanceStore,
    job_id: str,
    pipeline_name: str,
    definition: dict[str, Any],
    generated_entities: dict[str, Entity] | None,
) -> None:
    """Record this run's output entities, synthesizing one if none exist."""
    generations = definition.get("generations", [])
    entities = generated_entities
    if entities is None:
        if not generations:
            return
        entity_id = f"entity:{pipeline_name}-results:{job_id}"
        entities = {
            entity_id: Entity(id=entity_id, entity_description=generations[0].get("entity_description"))
        }

    role = generations[0]["role"] if generations else None
    generation_description_id = f"gendesc:{pipeline_name}:{role}" if role else None
    for entity_id, entity in entities.items():
        store.record_entity(entity)
        store.record_was_generated_by(
            entity_id,
            WasGeneratedBy(activity=job_id, role=role, generation_description=generation_description_id),
        )


def _infer_value_type(value: Any) -> str:
    """Name the Python type of one observed parameter value.

    `bool` is checked before `int` since `bool` subclasses `int`.

    Returns
    -------
    value_type : {"bool", "int", "float", "str"}
        The observed type's name.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _record_parameters(
    store: ProvenanceStore, job_id: str, pipeline_name: str, resolved_parameters: dict[str, Any]
) -> list[str]:
    """Record every resolved parameter's value and inferred description.

    Returns
    -------
    parameter_ids : `list` [`str`]
        The ids of the `Parameter` rows recorded.
    """
    parameters = []
    parameter_ids = []
    for name, value in resolved_parameters.items():
        parameter_description_id = f"paramdesc:{pipeline_name}:{name}"
        store.record_parameter_description(
            ParameterDescription(id=parameter_description_id, name=name, value_type=_infer_value_type(value))
        )
        parameter_id = f"{job_id}:param:{name}"
        parameters.append(
            Parameter(
                id=parameter_id, name=name, value=str(value), parameter_description=parameter_description_id
            )
        )
        parameter_ids.append(parameter_id)
    if parameters:
        store.record_parameters(job_id, parameters)
    return parameter_ids


def _record_config_file(store: ProvenanceStore, config: AppConfiguration) -> str | None:
    """Record the live `astrometrics.config.toml` this run read, if readable.

    The id is content-addressed, so an unedited file is never recorded
    twice, and content that cannot be read (deleted mid-run,
    permissions) is skipped rather than failing the run.

    Returns
    -------
    config_file_id : `str` or `None`
        The recorded file's id, or `None` if it could not be read.
    """
    path = config.config_file_path
    if path is None:
        return None
    try:
        content = path.read_bytes()
    except OSError as error:
        logger.debug("Could not read config file %r for provenance: %s", path, error)
        return None
    digest = hashlib.sha256(content).hexdigest()[:12]
    config_file_id = f"configfile:{digest}"
    store.record_config_file(
        ConfigFile(
            id=config_file_id,
            name=path.name,
            location=str(path),
            config_file_description="configfiledesc:astrometrics-toml",
        )
    )
    return config_file_id
