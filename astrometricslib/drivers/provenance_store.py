"""Repository for the IVOA provenance graph in astrometrics_log.db.

`ProvenanceStore` records and queries the PROV-DM objects defined in
`astrometricslib.models.provenance` -- which activity, using which
software version and which inputs, produced a given data product.

It lives in the same physical database as `LoggerInterface`'s job
tracking (`AppConfiguration.get_logs_db_path()`), since an `Activity` is
a superset of what a job row already tracks (a job's id becomes the
Activity's id, its start/end become the Activity's start/end), but it
is a separate module with its own tables: `LoggerInterface` is live,
operational job tracking (status, progress, log text); this is the
historical provenance graph.

Every public `record_*` method fails soft -- it logs and returns rather
than raising -- so a storage problem here can never turn a pipeline run
that otherwise succeeded into a failure. This mirrors
`astrometricslib.drivers.job_logging.JobHandle`'s own philosophy.
"""

import logging
import sqlite3
from datetime import datetime
from typing import Any

from astrometricslib.models.provenance import (
    Activity,
    ActivityDescription,
    Agent,
    AgentType,
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
    WasAssociatedWith,
    WasConfiguredBy,
    WasGeneratedBy,
)

logger = logging.getLogger(__name__)

# The five entity "kinds" this codebase's pipelines produce, seeded once
# so every run's aggregate output entity can reference one of them
# without each pipeline having to register its own.
_SEEDED_ENTITY_DESCRIPTIONS: tuple[EntityDescription, ...] = (
    DatasetDescription(
        id="entitydesc:stacked-image",
        name="StackedImage",
        description="A combined FITS image produced by one stacking run.",
        type="Dataset",
        content_type="image/fits",
    ),
    EntityDescription(
        id="entitydesc:photometry-results",
        name="PhotometryResults",
        description="Per-star light curves produced by one photometry run.",
        type="ResultSet",
    ),
    EntityDescription(
        id="entitydesc:spectroscopy-results",
        name="SpectroscopyResults",
        description="Per-star extracted spectra produced by one spectroscopy run.",
        type="ResultSet",
    ),
    EntityDescription(
        id="entitydesc:catalog-match-results",
        name="CatalogMatchResults",
        description="Per-star catalog-match confidence produced by one astrometry run.",
        type="ResultSet",
    ),
    EntityDescription(
        id="entitydesc:asteroid-candidate-list",
        name="AsteroidCandidateList",
        description="Moving-object candidates produced by one asteroid-detection run.",
        type="Collection",
    ),
)

_SEEDED_CONFIG_FILE_DESCRIPTION = ConfigFileDescription(
    id="configfiledesc:astrometrics-toml",
    name="astrometrics.config.toml",
    type="TOML",
    description=(
        "The TOML configuration file supplying observatory, camera, and "
        "processing defaults that resolved_parameters are partly derived from."
    ),
)


class ProvenanceStore:
    """Records and queries the IVOA provenance graph in astrometrics_log.db.

    Attributes
    ----------
    db_path : `str`
        Filesystem path to the SQLite database backing this
        repository.
    """

    def __init__(self, db_path: str):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the repository and ensure its tables exist.

        Parameters
        ----------
        db_path : `str`
            Filesystem path to the astrometrics_log.db SQLite database
            (see `AppConfiguration.get_logs_db_path()`).
        """
        self.db_path = db_path
        self._init_db()

    def _connect(self, timeout: float = 30.0) -> sqlite3.Connection:
        """Open a connection to astrometrics_log.db with WAL mode.

        Mirrors `LoggerInterface._connect`: WAL lets readers and
        writers proceed concurrently, and the busy timeout makes a
        concurrent writer retry briefly instead of immediately raising
        "database is locked".

        Returns
        -------
        connection : `sqlite3.Connection`
            Open connection with WAL mode, normal synchronous mode, a
            30-second busy timeout, and row access by column name.
        """
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_db(self) -> None:
        """Create every provenance table if missing, then seed registries."""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_agent (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    comment TEXT,
                    email TEXT,
                    affiliation TEXT,
                    phone TEXT,
                    address TEXT,
                    url TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_activity_description (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT,
                    type TEXT,
                    subtype TEXT,
                    description TEXT,
                    docurl TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_activity (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    target_id TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    comment TEXT,
                    activity_description_id TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prov_activity_target ON prov_activity(target_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_activity_informant (
                    activity_id TEXT NOT NULL,
                    informant_activity_id TEXT NOT NULL,
                    PRIMARY KEY (activity_id, informant_activity_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_entity_description (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    docurl TEXT,
                    type TEXT,
                    content_type TEXT,
                    value_type TEXT,
                    unit TEXT,
                    ucd TEXT,
                    utype TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_entity (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT,
                    location TEXT,
                    value TEXT,
                    generated_at_time TEXT,
                    invalidated_at_time TEXT,
                    comment TEXT,
                    entity_description_id TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_entity_used_entity (
                    entity_id TEXT NOT NULL,
                    used_entity_id TEXT NOT NULL,
                    PRIMARY KEY (entity_id, used_entity_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_usage_description (
                    id TEXT PRIMARY KEY,
                    activity_description_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    description TEXT,
                    type TEXT,
                    multiplicity TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_usage_description_entity_description (
                    usage_description_id TEXT NOT NULL,
                    entity_description_id TEXT NOT NULL,
                    PRIMARY KEY (usage_description_id, entity_description_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_generation_description (
                    id TEXT PRIMARY KEY,
                    activity_description_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    description TEXT,
                    type TEXT,
                    multiplicity TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_generation_description_entity_description (
                    generation_description_id TEXT NOT NULL,
                    entity_description_id TEXT NOT NULL,
                    PRIMARY KEY (generation_description_id, entity_description_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_used (
                    activity_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    role TEXT,
                    time TEXT,
                    usage_description_id TEXT,
                    PRIMARY KEY (activity_id, entity_id, role)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_was_generated_by (
                    entity_id TEXT PRIMARY KEY,
                    activity_id TEXT NOT NULL,
                    role TEXT,
                    generation_description_id TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_was_associated_with (
                    activity_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    role TEXT,
                    PRIMARY KEY (activity_id, agent_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_was_attributed_to (
                    entity_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    role TEXT,
                    PRIMARY KEY (entity_id, agent_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_parameter_description (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    unit TEXT,
                    ucd TEXT,
                    utype TEXT,
                    min TEXT,
                    max TEXT,
                    default_value TEXT,
                    options TEXT,
                    description TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_parameter (
                    id TEXT PRIMARY KEY,
                    activity_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    parameter_description_id TEXT
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_prov_parameter_activity ON prov_parameter(activity_id)"
            )

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_config_file (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    comment TEXT,
                    config_file_description_id TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_config_file_description (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    docurl TEXT,
                    type TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_was_configured_by (
                    activity_id TEXT NOT NULL,
                    artefact_type TEXT NOT NULL,
                    config_file_id TEXT,
                    PRIMARY KEY (activity_id, artefact_type)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prov_was_configured_by_parameter (
                    activity_id TEXT NOT NULL,
                    parameter_id TEXT NOT NULL,
                    PRIMARY KEY (activity_id, parameter_id)
                )
            """)

            conn.commit()
            conn.close()
        except Exception as error:
            logger.error("Error initializing provenance database at %s: %s", self.db_path, error)
            raise error

        for entity_description in _SEEDED_ENTITY_DESCRIPTIONS:
            self.record_entity_description(entity_description)
        self.record_config_file_description(_SEEDED_CONFIG_FILE_DESCRIPTION)

    # -- Agents --------------------------------------------------------

    def record_agent(self, agent: Agent) -> None:
        """Record a software/person/organization identity, if not known yet.

        Agents are immutable once created and reused across every run
        of the same pipeline version, so this never overwrites an
        existing row.

        Parameters
        ----------
        agent : `Agent`
            The agent to record.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_agent
                    (id, name, type, comment, email, affiliation, phone, address, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent.id,
                    agent.name,
                    agent.type.value,
                    agent.comment,
                    agent.email,
                    agent.affiliation,
                    agent.phone,
                    agent.address,
                    agent.url,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record agent %r: %s", agent.id, error)

    def get_agent_for_activity(self, activity_id: str) -> Agent | None:
        """Find the agent responsible for one activity.

        Parameters
        ----------
        activity_id : `str`
            The activity to look up.

        Returns
        -------
        agent : `Agent` or `None`
            The first associated agent found, or `None` if the
            activity has none recorded (including a legacy activity
            recorded before this store existed).
        """
        try:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT prov_agent.* FROM prov_agent
                JOIN prov_was_associated_with
                    ON prov_was_associated_with.agent_id = prov_agent.id
                WHERE prov_was_associated_with.activity_id = ?
                LIMIT 1
                """,
                (activity_id,),
            ).fetchone()
            conn.close()
            return self._row_to_agent(row) if row else None
        except Exception as error:
            logger.warning("Could not look up agent for activity %r: %s", activity_id, error)
            return None

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> Agent:
        return Agent(
            id=row["id"],
            name=row["name"],
            type=AgentType(row["type"]),
            comment=row["comment"],
            email=row["email"],
            affiliation=row["affiliation"],
            phone=row["phone"],
            address=row["address"],
            url=row["url"],
        )

    # -- Activities ------------------------------------------------------

    def record_activity(self, activity: Activity, target_id: str) -> None:
        """Record one pipeline run and its informant chain.

        Parameters
        ----------
        activity : `Activity`
            The run to record. `activity.id` is the run's `job_id`.
        target_id : `str`
            The target this run processed.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR REPLACE INTO prov_activity
                    (id, name, target_id, start_time, end_time, comment, activity_description_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity.id,
                    activity.name,
                    target_id,
                    activity.start_time.isoformat() if activity.start_time else None,
                    activity.end_time.isoformat() if activity.end_time else None,
                    activity.comment,
                    activity.activity_description,
                ),
            )
            for informant_id in activity.informant:
                conn.execute(
                    "INSERT OR IGNORE INTO prov_activity_informant (activity_id, informant_activity_id) "
                    "VALUES (?, ?)",
                    (activity.id, informant_id),
                )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record activity %r: %s", activity.id, error)

    def get_activity(self, activity_id: str) -> Activity | None:
        """Retrieve one recorded pipeline run.

        Parameters
        ----------
        activity_id : `str`
            The run's `job_id`.

        Returns
        -------
        activity : `Activity` or `None`
            The matching run, or `None` if it isn't recorded.
        """
        try:
            conn = self._connect()
            row = conn.execute("SELECT * FROM prov_activity WHERE id = ?", (activity_id,)).fetchone()
            if row is None:
                conn.close()
                return None
            informants = [
                informant_row["informant_activity_id"]
                for informant_row in conn.execute(
                    "SELECT informant_activity_id FROM prov_activity_informant WHERE activity_id = ?",
                    (activity_id,),
                ).fetchall()
            ]
            used = [
                Used(
                    entity=used_row["entity_id"],
                    role=used_row["role"],
                    usage_description=used_row["usage_description_id"],
                )
                for used_row in conn.execute(
                    "SELECT entity_id, role, usage_description_id FROM prov_used WHERE activity_id = ?",
                    (activity_id,),
                ).fetchall()
            ]
            conn.close()
            return Activity(
                id=row["id"],
                name=row["name"],
                start_time=datetime.fromisoformat(row["start_time"]) if row["start_time"] else None,
                end_time=datetime.fromisoformat(row["end_time"]) if row["end_time"] else None,
                comment=row["comment"],
                activity_description=row["activity_description_id"],
                informant=informants,
                used=used,
            )
        except Exception as error:
            logger.warning("Could not retrieve activity %r: %s", activity_id, error)
            return None

    def get_lineage(self, target_id: str) -> list[Activity]:
        """Return every recorded activity for one target, newest first.

        Parameters
        ----------
        target_id : `str`
            The target to look up.

        Returns
        -------
        activities : `list` [`Activity`]
            The target's recorded runs, most recent first. Empty if
            none are recorded or the query fails.
        """
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id FROM prov_activity WHERE target_id = ? ORDER BY start_time DESC, id DESC",
                (target_id,),
            ).fetchall()
            conn.close()
            activities = [self.get_activity(row["id"]) for row in rows]
            return [activity for activity in activities if activity is not None]
        except Exception as error:
            logger.warning("Could not retrieve lineage for target %r: %s", target_id, error)
            return []

    def get_generated_entity_ids(self, activity_id: str) -> list[str]:
        """List the ids of every entity one activity is recorded as generating.

        Parameters
        ----------
        activity_id : `str`
            The generating activity.

        Returns
        -------
        entity_ids : `list` [`str`]
            Ids of the entities this activity produced. Empty if none
            are recorded or the query fails.
        """
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT entity_id FROM prov_was_generated_by WHERE activity_id = ?", (activity_id,)
            ).fetchall()
            conn.close()
            return [row["entity_id"] for row in rows]
        except Exception as error:
            logger.warning("Could not list entities generated by activity %r: %s", activity_id, error)
            return []

    # -- Entities ----------------------------------------------------------

    def record_entity(self, entity: Entity) -> None:
        """Record one data product and what it was derived from.

        Parameters
        ----------
        entity : `Entity`
            The entity to record. May be a `DatasetEntity`,
            `ValueEntity`, `Collection`, or plain `Entity`.
        """
        kind = "generic"
        value = None
        if isinstance(entity, DatasetEntity):
            kind = "dataset"
        elif hasattr(entity, "value"):
            kind = "value"
            value = entity.value
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR REPLACE INTO prov_entity
                    (id, kind, name, location, value, generated_at_time, invalidated_at_time,
                     comment, entity_description_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.id,
                    kind,
                    entity.name,
                    entity.location,
                    value,
                    entity.generated_at_time.isoformat() if entity.generated_at_time else None,
                    entity.invalidated_at_time.isoformat() if entity.invalidated_at_time else None,
                    entity.comment,
                    entity.entity_description,
                ),
            )
            for used_entity_id in entity.used_entity:
                conn.execute(
                    "INSERT OR IGNORE INTO prov_entity_used_entity (entity_id, used_entity_id) VALUES (?, ?)",
                    (entity.id, used_entity_id),
                )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record entity %r: %s", entity.id, error)

    def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieve one recorded data product.

        Parameters
        ----------
        entity_id : `str`
            The entity to look up.

        Returns
        -------
        entity : `Entity` or `None`
            The matching entity (as a `DatasetEntity`/`ValueEntity`
            when its kind calls for it), or `None` if it isn't
            recorded.
        """
        try:
            conn = self._connect()
            row = conn.execute("SELECT * FROM prov_entity WHERE id = ?", (entity_id,)).fetchone()
            if row is None:
                conn.close()
                return None
            used_entities = [
                used_row["used_entity_id"]
                for used_row in conn.execute(
                    "SELECT used_entity_id FROM prov_entity_used_entity WHERE entity_id = ?",
                    (entity_id,),
                ).fetchall()
            ]
            conn.close()
            fields: dict[str, Any] = {
                "id": row["id"],
                "name": row["name"],
                "location": row["location"],
                "generated_at_time": datetime.fromisoformat(row["generated_at_time"])
                if row["generated_at_time"]
                else None,
                "invalidated_at_time": datetime.fromisoformat(row["invalidated_at_time"])
                if row["invalidated_at_time"]
                else None,
                "comment": row["comment"],
                "entity_description": row["entity_description_id"],
                "used_entity": used_entities,
            }
            if row["kind"] == "dataset":
                return DatasetEntity(**fields)
            if row["kind"] == "value":
                return _value_entity(fields, row["value"])
            return Entity(**fields)
        except Exception as error:
            logger.warning("Could not retrieve entity %r: %s", entity_id, error)
            return None

    # -- Relations -----------------------------------------------------

    def record_used(self, activity_id: str, used: Used) -> None:
        """Record that one activity consumed one entity as input.

        Parameters
        ----------
        activity_id : `str`
            The consuming activity.
        used : `Used`
            The consumed entity and its role.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_used (activity_id, entity_id, role, time, usage_description_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    activity_id,
                    used.entity,
                    used.role,
                    used.time.isoformat() if used.time else None,
                    used.usage_description,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record usage of %r by %r: %s", used.entity, activity_id, error)

    def record_was_generated_by(self, entity_id: str, generated_by: WasGeneratedBy) -> None:
        """Record the one activity that produced one entity.

        Overwrites any previous generating activity for `entity_id`,
        matching the spec's 0..1 cardinality (an entity has at most one
        generating activity) via the table's primary key on `entity_id`.

        Parameters
        ----------
        entity_id : `str`
            The produced entity.
        generated_by : `WasGeneratedBy`
            The producing activity and the entity's role in it.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR REPLACE INTO prov_was_generated_by
                    (entity_id, activity_id, role, generation_description_id)
                VALUES (?, ?, ?, ?)
                """,
                (entity_id, generated_by.activity, generated_by.role, generated_by.generation_description),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record generation of %r: %s", entity_id, error)

    def record_was_associated_with(self, activity_id: str, association: WasAssociatedWith) -> None:
        """Record that one agent is responsible for one activity.

        Parameters
        ----------
        activity_id : `str`
            The activity.
        association : `WasAssociatedWith`
            The responsible agent and its role.
        """
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR IGNORE INTO prov_was_associated_with (activity_id, agent_id, role) "
                "VALUES (?, ?, ?)",
                (activity_id, association.agent, association.role),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record association for activity %r: %s", activity_id, error)

    def record_was_attributed_to(self, entity_id: str, agent_id: str, role: str | None = None) -> None:
        """Record that an agent is responsible for an entity with no generator.

        Parameters
        ----------
        entity_id : `str`
            The entity.
        agent_id : `str`
            The responsible agent.
        role : `str`, optional
            The agent's function with respect to the entity.
        """
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR IGNORE INTO prov_was_attributed_to (entity_id, agent_id, role) VALUES (?, ?, ?)",
                (entity_id, agent_id, role),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record attribution for entity %r: %s", entity_id, error)

    # -- Description layer -----------------------------------------------

    def ensure_activity_description(self, description: ActivityDescription) -> None:
        """Record what one kind of pipeline run looks like, if not known yet.

        Parameters
        ----------
        description : `ActivityDescription`
            The pipeline-and-version description to record.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_activity_description
                    (id, name, version, type, subtype, description, docurl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    description.id,
                    description.name,
                    description.version,
                    description.type,
                    description.subtype,
                    description.description,
                    description.docurl,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record activity description %r: %s", description.id, error)

    def record_entity_description(self, description: EntityDescription) -> None:
        """Record what one kind of entity looks like, if not already known.

        Parameters
        ----------
        description : `EntityDescription`
            The description to record. May be a `DatasetDescription`,
            `ValueDescription`, or plain `EntityDescription`.
        """
        kind = "generic"
        content_type = None
        value_type = None
        unit = ucd = utype = None
        if isinstance(description, DatasetDescription):
            kind = "dataset"
            content_type = description.content_type
        elif isinstance(description, ValueDescription):
            kind = "value"
            value_type, unit, ucd, utype = (
                description.value_type,
                description.unit,
                description.ucd,
                description.utype,
            )
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_entity_description
                    (id, kind, name, description, docurl, type, content_type, value_type, unit, ucd, utype)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    description.id,
                    kind,
                    description.name,
                    description.description,
                    description.docurl,
                    description.type,
                    content_type,
                    value_type,
                    unit,
                    ucd,
                    utype,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record entity description %r: %s", description.id, error)

    def record_usage_description(self, activity_description_id: str, description: UsageDescription) -> None:
        """Record what one kind of pipeline input looks like, if not known yet.

        Parameters
        ----------
        activity_description_id : `str`
            The pipeline-and-version description this input belongs to.
        description : `UsageDescription`
            The description to record.
        """
        self._record_usage_or_generation_description(
            "prov_usage_description",
            "prov_usage_description_entity_description",
            "usage_description_id",
            activity_description_id,
            description,
        )

    def record_generation_description(
        self, activity_description_id: str, description: GenerationDescription
    ) -> None:
        """Record what one kind of pipeline output looks like, if new.

        Parameters
        ----------
        activity_description_id : `str`
            The pipeline-and-version description this output belongs to.
        description : `GenerationDescription`
            The description to record.
        """
        self._record_usage_or_generation_description(
            "prov_generation_description",
            "prov_generation_description_entity_description",
            "generation_description_id",
            activity_description_id,
            description,
        )

    def _record_usage_or_generation_description(
        self,
        table: str,
        join_table: str,
        id_column: str,
        activity_description_id: str,
        description: UsageDescription | GenerationDescription,
    ) -> None:
        # table/join_table/id_column are always one of the two fixed literal
        # pairs passed by the two public methods above, never user input.
        try:
            conn = self._connect()
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {table}
                    (id, activity_description_id, role, description, type, multiplicity)
                VALUES (?, ?, ?, ?, ?, ?)
                """,  # ruff: ignore[hardcoded-sql-expression]
                (
                    description.id,
                    activity_description_id,
                    description.role,
                    description.description,
                    description.type,
                    description.multiplicity,
                ),
            )
            for entity_description_id in description.entity_description:
                conn.execute(
                    f"INSERT OR IGNORE INTO {join_table} ({id_column}, entity_description_id) VALUES (?, ?)",  # ruff: ignore[hardcoded-sql-expression]
                    (description.id, entity_description_id),
                )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record %s %r: %s", table, description.id, error)

    def record_parameter_description(self, description: ParameterDescription) -> None:
        """Record what one kind of parameter looks like, first-write-wins.

        A later run with the same parameter name never overwrites a
        description that already exists -- richer fields filled in by
        hand stay intact.

        Parameters
        ----------
        description : `ParameterDescription`
            The description to record.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_parameter_description
                    (id, name, value_type, unit, ucd, utype, min, max, default_value, options, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    description.id,
                    description.name,
                    description.value_type,
                    description.unit,
                    description.ucd,
                    description.utype,
                    description.min,
                    description.max,
                    description.default,
                    ",".join(description.options) if description.options else None,
                    description.description,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record parameter description %r: %s", description.id, error)

    def record_parameters(self, activity_id: str, parameters: list[Parameter]) -> None:
        """Record the settings one activity was actually run with.

        Parameters
        ----------
        activity_id : `str`
            The configured activity.
        parameters : `list` [`Parameter`]
            The resolved parameter values to record.
        """
        try:
            conn = self._connect()
            for parameter in parameters:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO prov_parameter
                        (id, activity_id, name, value, parameter_description_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        parameter.id,
                        activity_id,
                        parameter.name,
                        parameter.value,
                        parameter.parameter_description,
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record parameters for activity %r: %s", activity_id, error)

    def record_config_file_description(self, description: ConfigFileDescription) -> None:
        """Record what one kind of configuration file looks like, if new.

        Parameters
        ----------
        description : `ConfigFileDescription`
            The description to record.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_config_file_description (id, name, description, docurl, type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    description.id,
                    description.name,
                    description.description,
                    description.docurl,
                    description.type,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record config file description %r: %s", description.id, error)

    def record_config_file(self, config_file: ConfigFile) -> None:
        """Record one configuration file, if its content is new.

        `config_file.id` is content-addressed, so an unedited file is
        never recorded twice, while an actual edit produces a new,
        distinct row.

        Parameters
        ----------
        config_file : `ConfigFile`
            The file to record.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR IGNORE INTO prov_config_file
                    (id, name, location, comment, config_file_description_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    config_file.id,
                    config_file.name,
                    config_file.location,
                    config_file.comment,
                    config_file.config_file_description,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record config file %r: %s", config_file.id, error)

    def record_was_configured_by(self, activity_id: str, configured_by: WasConfiguredBy) -> None:
        """Record one configuration artefact -- parameters or a file -- used.

        Parameters
        ----------
        activity_id : `str`
            The configured activity.
        configured_by : `WasConfiguredBy`
            The artefact -- either resolved parameters or a config
            file -- and its type.
        """
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR REPLACE INTO prov_was_configured_by (activity_id, artefact_type, config_file_id)
                VALUES (?, ?, ?)
                """,
                (activity_id, configured_by.artefact_type, configured_by.config_file),
            )
            for parameter_id in configured_by.parameters:
                conn.execute(
                    "INSERT OR IGNORE INTO prov_was_configured_by_parameter (activity_id, parameter_id) "
                    "VALUES (?, ?)",
                    (activity_id, parameter_id),
                )
            conn.commit()
            conn.close()
        except Exception as error:
            logger.warning("Could not record configuration for activity %r: %s", activity_id, error)


def export_target_lineage_as_prov_xml(target_id: str, store: ProvenanceStore) -> str:
    """Export one target's full lineage as real W3C PROV-XML.

    Built for spec-conformance testing, not as this codebase's primary
    storage or query layer -- `ProvenanceStore`'s own relational schema
    remains that. Uses the `prov` package (the base W3C PROV library) so
    the resulting document can be validated against the actual published
    PROV-XML schema, a genuine external conformance check rather than an
    internally invented one.

    Ids are not valid PROV `QualifiedName`s as this codebase stores them
    (for example ``"entity:stack-image:M13:abc123"`` has more than one
    colon), so each is re-encoded as a local name under one fixed
    ``id:`` namespace.

    Parameters
    ----------
    target_id : `str`
        The target to export lineage for.
    store : `ProvenanceStore`
        Where to read the lineage from.

    Returns
    -------
    document_xml : `str`
        The serialized PROV-XML document.
    """
    import prov.model as prov_model

    document = prov_model.ProvDocument()
    document.add_namespace("id", "http://astrometricslib.local/prov#")

    seen_entity_ids: set[str] = set()
    seen_agent_ids: set[str] = set()

    def qname(raw_id: str) -> str:
        return f"id:{raw_id.replace(':', '_')}"

    def ensure_entity(entity_id: str) -> None:
        if entity_id not in seen_entity_ids:
            document.entity(qname(entity_id))
            seen_entity_ids.add(entity_id)

    for activity in store.get_lineage(target_id):
        document.activity(qname(activity.id), startTime=activity.start_time, endTime=activity.end_time)

        agent = store.get_agent_for_activity(activity.id)
        if agent is not None:
            if agent.id not in seen_agent_ids:
                document.agent(qname(agent.id))
                seen_agent_ids.add(agent.id)
            document.wasAssociatedWith(qname(activity.id), qname(agent.id))

        for used in activity.used:
            ensure_entity(used.entity)
            document.used(qname(activity.id), qname(used.entity))

        for entity_id in store.get_generated_entity_ids(activity.id):
            ensure_entity(entity_id)
            document.wasGeneratedBy(qname(entity_id), qname(activity.id))

        for informant_id in activity.informant:
            document.wasInformedBy(qname(activity.id), qname(informant_id))

    return document.serialize(format="xml")


def _value_entity(fields: dict[str, Any], value: str) -> Entity:
    """Build a `ValueEntity` from a base field dict plus its stored value.

    Kept as a plain function rather than inlined so `get_entity` stays
    readable.

    Returns
    -------
    entity : `astrometricslib.models.provenance.ValueEntity`
        The reconstructed value entity.
    """
    from astrometricslib.models.provenance import ValueEntity

    return ValueEntity(**fields, value=value)
