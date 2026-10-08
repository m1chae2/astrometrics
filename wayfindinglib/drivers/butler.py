"""Purpose: wayfindinglib's single storage path.

Description: `DiskButler` is the one place wayfindinglib reads and writes
its own records. It follows the Rubin Observatory Butler pattern that
astrometricslib uses: a small get/put/exists interface keyed by a dataset
type name. It implements astrometricslib's shared `AbstractButler` and
hands every call to astrometricslib's generic SQLite `Butler`, which keeps
one table per dataset type in wayfindinglib's own ``wayfinding.db`` file.
That file lives in wayfindinglib's own library folder, kept apart from
astrometricslib's library folder.

Each dataset type maps to a table and a pydantic model class in
`_DATASET_TYPES`. A record's key is its ``id`` field, except for
``calibration_stats``, whose key is ``camera_id``.
"""

import configparser
from pathlib import Path
from typing import Any

from astrometricslib import AbstractButler, DatasetSpec, InvalidArgumentError
from astrometricslib import Butler as _GenericButler
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
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.observation_session import ObservationSession
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis

_DATASET_TYPES: dict[str, tuple[str, type]] = {
    "observation_session": ("wayfinding_observation_sessions", ObservationSession),
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
    "guiding_spectrum_analysis": ("guiding_spectrum_analyses", GuidingSpectrumAnalysis),
    "ekos_session_context": ("ekos_session_contexts", EkosSessionContext),
    "guiding_run": ("guiding_runs", GuidingRunSummary),
}
"""Maps a dataset_type string to its (table_name, model_class).

`calibration_stats` uses `camera_id` as its key, because one record
holds the calibration inventory of one camera
(`Wayfinding_Library_Architecture.md` §2.2.2).
"""

_ID_FIELD_FOR: dict[str, str] = {"calibration_stats": "camera_id"}
"""Overrides the primary-key field name for dataset types whose natural
key is not `id` -- everything else defaults to `id`."""


def _wayfinding_library_path(app_config: Any) -> Path:
    """Return wayfindinglib's own library folder, creating it if needed.

    The folder comes from the ``path`` option of the
    ``[Wayfinding Library]`` configuration section. A relative path is
    taken relative to the project root. Without that option the folder
    is ``wayfindinglib/library`` under the project root.

    Parameters
    ----------
    app_config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    path : `Path`
        Absolute path to the folder.
    """
    try:
        path = Path(app_config.app_config.get("Wayfinding Library", "path"))
        if not path.is_absolute():
            path = (app_config.get_project_root() / path).absolute()
    except configparser.NoSectionError, configparser.NoOptionError, KeyError:
        path = app_config.get_project_root() / "wayfindinglib" / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path.absolute()


class DiskButler(AbstractButler):
    """Local SQLite-backed Butler implementation for wayfindinglib."""

    def __init__(self, app_config: Any = None) -> None:
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
    def library_path(self) -> Path:
        """Wayfindinglib's own library folder, created if needed.

        Holds ``wayfinding.db`` and the files wayfindinglib keeps next to
        it, such as downloaded Ekos logs and the latest centering frame.
        """
        return _wayfinding_library_path(self.config)

    @property
    def _generic(self) -> _GenericButler:
        """Build the shared generic Butler on first use.

        Built here rather than in `__init__` so that a `DiskButler` made
        with a stand-in `config` (as some tests do, to check error paths
        without ever calling get or put) does not look up a real library
        folder.
        """
        if self.__generic is None:
            specs = {
                dataset_type: DatasetSpec(
                    table_name=table_name,
                    model_class=model_class,
                    id_field=_ID_FIELD_FOR.get(dataset_type, "id"),
                    serializer=lambda obj: obj.model_dump(mode="json", by_alias=True),
                )
                for dataset_type, (table_name, model_class) in _DATASET_TYPES.items()
            }
            self.__generic = _GenericButler(
                self.config, db_name="wayfinding.db", db_dir=str(self.library_path), specs=specs
            )
        return self.__generic

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Retrieve the dataset a selector identifies.

        Parameters
        ----------
        dataset_type : `str`
            The dataset kind to read, one of the keys of `_DATASET_TYPES`.
        selector : `dict`
            Names the record: a ``"session_id"`` key for
            ``"observation_session"``, otherwise an ``"id"`` key (or the
            record's own key field, ``"camera_id"`` for
            ``"calibration_stats"``).

        Returns
        -------
        dataset : `Any`
            The record, or `None` if there is none.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        if dataset_type == "observation_session":
            return self._generic.get(dataset_type, {"id": selector.get("session_id", "")})
        id_field = _ID_FIELD_FOR.get(dataset_type, "id")
        model_id = selector.get("id") or selector.get(id_field, "")
        return self._generic.get(dataset_type, {"id": model_id})

    def get_all(self, dataset_type: str) -> list[Any]:
        """Retrieve every recorded instance of a dataset type.

        Parameters
        ----------
        dataset_type : `str`
            The dataset kind to read, one of the keys of `_DATASET_TYPES`.

        Returns
        -------
        datasets : `list`
            Every recorded instance of that dataset type.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        return self._generic.get_all(dataset_type)

    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Record a dataset under a given type.

        Parameters
        ----------
        obj : `Any`
            The model instance to record.
        dataset_type : `str`
            The dataset kind to write, one of the keys of `_DATASET_TYPES`.
        selector : `dict`
            Not used: the record's own key field (``obj.id``, or
            ``obj.camera_id`` for ``"calibration_stats"``) is its key.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        self._generic.put(obj, dataset_type)

    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Check whether the dataset a selector identifies exists.

        Parameters
        ----------
        dataset_type : `str`
            The dataset kind to check.
        selector : `dict`
            Names the record, as for `get`.

        Returns
        -------
        exists : `bool`
            `True` if a matching record is found.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        return self.get(dataset_type, selector) is not None
