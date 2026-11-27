"""The store of observatory configurations in astrometrics.db.

Each `ObservatoryConfiguration` is one row of the table
``observatory_configurations``: its id, its camera-and-focal-length key (a
column, so frames can be matched without reading every row's JSON) and the
whole record as JSON. The table is small (one row per set of equipment the
observatory ever used), so a reader loads it whole.

A configuration is never deleted. A save checks the revision: `replace`
saves only when the stored revision is the one the caller read, under the
same file lock every writer of this table takes, so two programs cannot
both change one configuration from the same starting point.
"""

from typing import Any

from astrometricslib.foundation.errors import ConflictError, NotFoundError
from astrometricslib.foundation.storage.butler import Butler, DatasetSpec
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration

DATASET_TYPE = "observatory_configuration"
"""The dataset name the table is registered under."""

TABLE_NAME = "observatory_configurations"
"""The SQLite table that holds the configurations."""


def _payload(configuration: ObservatoryConfiguration) -> dict[str, Any]:
    """Give the JSON data saved for one configuration.

    Parameters
    ----------
    configuration : `ObservatoryConfiguration`
        The configuration.

    Returns
    -------
    payload : `dict`
        Its fields as plain JSON values.
    """
    return configuration.model_dump(mode="json")


def _columns(configuration: ObservatoryConfiguration) -> dict[str, Any]:
    """Give the searchable columns of one configuration.

    Parameters
    ----------
    configuration : `ObservatoryConfiguration`
        The configuration.

    Returns
    -------
    columns : `dict`
        ``configuration_key``, the camera-and-focal-length key.
    """
    return {"configuration_key": configuration.configuration_key}


class ObservatoryConfigurationStore:
    """Read and save observatory configurations.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings; they give the library folder that holds
        ``astrometrics.db``.
    """

    def __init__(self, config: Any) -> None:
        self._butler = Butler(
            config,
            db_name="astrometrics.db",
            specs={
                DATASET_TYPE: DatasetSpec(
                    table_name=TABLE_NAME,
                    model_class=ObservatoryConfiguration,
                    extra_column_types={"configuration_key": "TEXT"},
                    extra_columns=_columns,
                    indexed_columns=("configuration_key",),
                    serializer=_payload,
                )
            },
        )

    def all(self) -> list[ObservatoryConfiguration]:
        """Read every stored configuration.

        Returns
        -------
        configurations : `list` [`ObservatoryConfiguration`]
            Every configuration, oldest start date first (an unknown start
            date sorts first), then by id.
        """
        configurations = self._butler.get_all(DATASET_TYPE)
        return sorted(
            configurations, key=lambda item: (item.valid_from is not None, item.valid_from, item.id)
        )

    def get(self, configuration_id: str) -> ObservatoryConfiguration | None:
        """Read one configuration.

        Parameters
        ----------
        configuration_id : `str`
            Its id.

        Returns
        -------
        configuration : `ObservatoryConfiguration` or `None`
            The configuration, or `None` when no configuration has that id.
        """
        return self._butler.get(DATASET_TYPE, {"id": configuration_id})

    def insert(self, configuration: ObservatoryConfiguration) -> None:
        """Save a new configuration.

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            The configuration, with its id and revision already set.

        Notes
        -----
        Raises `ConflictError` if a configuration with that id is already
        stored.
        """

        def _only_new(existing: ObservatoryConfiguration | None, new: ObservatoryConfiguration) -> Any:
            """Refuse to overwrite a stored configuration.

            Parameters
            ----------
            existing : `ObservatoryConfiguration` or `None`
                The stored row with the new one's id, if any.
            new : `ObservatoryConfiguration`
                The configuration to save.

            Returns
            -------
            saved : `ObservatoryConfiguration`
                `new`, when nothing is stored under its id.

            Raises
            ------
            ConflictError
                If a configuration is stored under its id.
            """
            if existing is not None:
                raise ConflictError(
                    f"An observatory configuration with id {new.id!r} is already stored.",
                    details={"configuration_id": new.id},
                )
            return new

        self._butler.merge_and_record(DATASET_TYPE, [configuration], _only_new)

    def replace(self, configuration: ObservatoryConfiguration, expected_revision: int) -> None:
        """Save a changed configuration over the stored one.

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            The changed configuration, with its new revision.
        expected_revision : `int`
            The revision the caller read. The save is refused when the
            stored one differs.

        Notes
        -----
        Raises `NotFoundError` if no configuration with that id is stored,
        and `ConflictError` if the stored revision is not
        `expected_revision`: someone else saved the configuration since it
        was read.
        """

        def _same_revision(existing: ObservatoryConfiguration | None, new: ObservatoryConfiguration) -> Any:
            """Refuse a save based on an old copy.

            Parameters
            ----------
            existing : `ObservatoryConfiguration` or `None`
                The stored row with the new one's id, if any.
            new : `ObservatoryConfiguration`
                The configuration to save.

            Returns
            -------
            saved : `ObservatoryConfiguration`
                `new`, when the stored revision is the expected one.

            Raises
            ------
            NotFoundError
                If nothing is stored under its id.
            ConflictError
                If the stored revision is another.
            """
            if existing is None:
                raise NotFoundError(
                    f"No observatory configuration with id {new.id!r}.", details={"configuration_id": new.id}
                )
            if existing.revision != expected_revision:
                raise ConflictError(
                    f"The observatory configuration {new.id!r} was changed since it was read "
                    f"(revision {existing.revision}, not {expected_revision}). Read it again and retry.",
                    details={"configuration_id": new.id, "stored_revision": existing.revision},
                )
            return new

        self._butler.merge_and_record(DATASET_TYPE, [configuration], _same_revision)
