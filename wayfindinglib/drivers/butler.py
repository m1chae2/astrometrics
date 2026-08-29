"""Purpose: wayfindinglib's data access layer.

Description: Follows the same Rubin Observatory Butler pattern
astrometricslib uses. A thin get/put/exists dataset-type dispatcher over
wayfindinglib/drivers/disk_interface.py. Deliberately its own minimal
abstract base rather than subclassing astrometricslib's four-method
AbstractButler (astrometricslib/data_access/butler.py) --
get_local_path is a FITS-file-path concern that doesn't apply to a
single SQLite-backed pydantic model.

"observation_session" routes to `disk_interface.save_wayfinding_session`/
`get_wayfinding_session`, serving
`wayfindinglib.models.session.observation_session.ObservationSession`
(the three-function redesign's session, with its queue, telescope_id,
camera_id, divergence records, etc.) under its own
table, physically distinct from the deprecated single-target
telemetry-only `observationlib.observation_session.ObservationSession`
that `disk_interface`'s original `save_observation_session`/etc.
functions still serve -- those are untouched, since
`observationlib.session_recorder.ObservationSessionRecorder` still calls
them directly, bypassing this Butler entirely, until it is relocated
onto the new model. Every other dataset type -- observation_package,
site_profile, enclosure, guider_calibration, focus_model,
delegation_policy, safety_rule_set, commissioning_run -- is dispatched
generically via `_GENERIC_DATASET_TYPES` to
`disk_interface.save_model`/`load_models`/`get_model`, keyed by the `id`
field in `coordinate`.
"""

from abc import ABC, abstractmethod
from typing import Any

from datastore.butler import Butler as _GenericButler
from datastore.butler import DatasetSpec
from wayfindinglib.drivers import disk_interface
from wayfindinglib.models.equipment_and_site.calibration import CalibrationStats
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.observation_package import ObservationPackage
from wayfindinglib.models.policy.commissioning import CommissioningRun
from wayfindinglib.models.policy.delegation import DelegationPolicy
from wayfindinglib.models.policy.safety import SafetyRuleSet
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent
from wayfindinglib.models.session.observation_session import ObservationSession

_GENERIC_DATASET_TYPES: dict[str, tuple[str, type]] = {
    "observation_package": ("observation_packages", ObservationPackage),
    "site_profile": ("site_profiles", SiteProfile),
    "enclosure": ("enclosures", Enclosure),
    "guider_calibration": ("guider_calibrations", GuiderCalibration),
    "focus_model": ("focus_models", FocusModel),
    "calibration_stats": ("calibration_stats", CalibrationStats),
    "delegation_policy": ("delegation_policies", DelegationPolicy),
    "safety_rule_set": ("safety_rule_sets", SafetyRuleSet),
    "divergence_record": ("divergence_records", DivergenceRecord),
    "commissioning_run": ("commissioning_runs", CommissioningRun),
    "guide_star_loss_event": ("guide_star_loss_events", GuideStarLossEvent),
}
"""Maps a dataset_type string to its (table_name, model_class).

`calibration_stats` uses `camera_id` as its coordinate/id, matching
`CalibrationStats.camera_id` being the natural key for one camera's
inventory (`Wayfinding_Library_Architecture.md` §2.2.2).
"""

_ID_FIELD_FOR: dict[str, str] = {"calibration_stats": "camera_id"}
"""Overrides the primary-key field name for dataset types whose natural
key is not `id` -- everything else defaults to `id`."""


class AbstractButler(ABC):
    """Minimal get/put/exists data access layer for wayfindinglib."""

    @abstractmethod
    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
        """Retrieve a dataset for a coordinate."""

    @abstractmethod
    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any]) -> None:
        """Persist a dataset under a given type and coordinate."""

    @abstractmethod
    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Check if the dataset exists for the coordinate."""


class DiskButler(AbstractButler):
    """Local SQLite-backed Butler implementation for wayfindinglib."""

    def __init__(self, app_config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the Butler with an application configuration.

        Parameters
        ----------
        app_config : `AppConfiguration`, optional
            Application configuration object. If `None` (default), the
            process-wide singleton from `get_configuration` is used.
        """
        if app_config is None:
            from astrometricslib import get_configuration

            app_config = get_configuration()
        self.config = app_config
        self.__generic: _GenericButler | None = None

    @property
    def _generic(self) -> _GenericButler:
        """Lazily build the shared generic Butler on first use.

        Deferred rather than built in `__init__` so constructing a
        `DiskButler` with a dummy/sentinel `config` (as some tests do,
        to exercise standalone-mode error paths without ever calling
        get/put) doesn't eagerly resolve a real library path.
        """
        if self.__generic is None:
            self.__generic = self._build_generic_butler(self.config)
        return self.__generic

    @staticmethod
    def _build_generic_butler(app_config: Any) -> _GenericButler:
        db_dir = str(disk_interface._wayfinding_library_path(app_config))
        specs: dict[str, DatasetSpec] = {
            "observation_session": DatasetSpec(
                table_name=disk_interface._WAYFINDING_SESSION_TABLE_NAME,
                model_class=ObservationSession,
                serializer=lambda obj: obj.model_dump(mode="json", by_alias=True),
            ),
        }
        for dataset_type, (table_name, model_class) in _GENERIC_DATASET_TYPES.items():
            specs[dataset_type] = DatasetSpec(
                table_name=table_name,
                model_class=model_class,
                id_field=_ID_FIELD_FOR.get(dataset_type, "id"),
                serializer=lambda obj: obj.model_dump(mode="json", by_alias=True),
            )
        return _GenericButler(app_config, db_name="wayfinding.db", db_dir=db_dir, specs=specs)

    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
        """Retrieve a dataset for a coordinate.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to retrieve: "observation_session",
            or one of the generic types in `_GENERIC_DATASET_TYPES`.
        coordinate : `dict`
            Data ID fields identifying which dataset instance to load;
            expects a "session_id" key for "observation_session", or an
            "id" key (or the dataset's natural key, e.g. "camera_id" for
            "calibration_stats") for generic types.

        Returns
        -------
        dataset : `Any`
            The hydrated dataset instance, or `None` if not found.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type == "observation_session":
            return self._generic.get("observation_session", {"id": coordinate.get("session_id", "")})
        if dataset_type in _GENERIC_DATASET_TYPES:
            id_field = _ID_FIELD_FOR.get(dataset_type, "id")
            model_id = coordinate.get("id") or coordinate.get(id_field, "")
            return self._generic.get(dataset_type, {"id": model_id})
        raise ValueError(f"Unknown dataset type: {dataset_type}")

    def get_all(self, dataset_type: str) -> list[Any]:
        """Retrieve every persisted instance of a dataset type.

        Parameters
        ----------
        dataset_type : `str`
            ``"observation_session"``, or one of the generic types in
            `_GENERIC_DATASET_TYPES`.

        Returns
        -------
        datasets : `list`
            Every persisted instance of that dataset type.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type == "observation_session":
            return self._generic.get_all("observation_session")
        if dataset_type not in _GENERIC_DATASET_TYPES:
            raise ValueError(f"Unknown generic dataset type: {dataset_type}")
        return self._generic.get_all(dataset_type)

    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any]) -> None:
        """Persist a dataset under a given type and coordinate.

        Parameters
        ----------
        obj : `Any`
            The model instance to persist.
        dataset_type : `str`
            Identifier of the dataset kind being written:
            "observation_session", or one of the generic types in
            `_GENERIC_DATASET_TYPES`.
        coordinate : `dict`
            Unused for "observation_session" -- the session's own `id`
            field is the primary key. Unused for generic types -- the
            object's own natural key (`obj.id`, or `obj.camera_id` for
            "calibration_stats") is the primary key.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type == "observation_session":
            self._generic.put(obj, "observation_session")
            return
        if dataset_type in _GENERIC_DATASET_TYPES:
            self._generic.put(obj, dataset_type)
            return
        raise ValueError(f"Write operation not supported on dataset type: {dataset_type}")

    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Check if the dataset exists for the coordinate.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to check.
        coordinate : `dict`
            Expects a "session_id" key for "observation_session", or an
            "id" key (or the dataset's natural key) for generic types.

        Returns
        -------
        exists : `bool`
            `True` if a matching dataset is found.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type != "observation_session" and dataset_type not in _GENERIC_DATASET_TYPES:
            raise ValueError(f"Unknown dataset type: {dataset_type}")
        return self.get(dataset_type, coordinate) is not None
