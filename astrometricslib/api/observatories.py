"""Main interface for the observatory's equipment and its configurations.

`Observatories` is `Astrometrics.observatories`. Its `configurations` child
(`ObservatoryConfigurations`) keeps every set of equipment the observatory
used together: a telescope, a main camera, and optionally a guide scope,
guide camera, focuser, filter wheel and mount, over a range of dates. Each
method is a thin wrapper of one function in
`astrometricslib.pipelines.shared.equipment`.

The first call seeds the configurations that the settings and the frames
already imply (see that module), once per program.
"""

import threading
from datetime import date, datetime
from typing import TYPE_CHECKING

from astrometricslib._internal.drivers.observatory_configuration_store import ObservatoryConfigurationStore
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.observatory_configuration import (
    Camera,
    ObservatoryConfiguration,
    Telescope,
)
from astrometricslib.models.page import Page

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog

__all__ = ["Observatories", "ObservatoryConfigurations"]


class ObservatoryConfigurations(MovedNames):
    """Every set of equipment the observatory used, past and present.

    Any change of equipment is a new configuration (`create`); only the
    name, notes and dates of a stored one change (`update`). Frames are
    chosen by configuration with
    ``astrometrics.targets.select_frames(target, configuration=...)``.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings. They give the library folder and the equipment the
        settings file lists.
    targets : `TargetCatalog`
        The targets, whose frames are matched to their configurations.
    """

    def __init__(self, config: AppConfiguration, *, targets: TargetCatalog) -> None:
        self._config = config
        self._targets = targets
        self._store = ObservatoryConfigurationStore(config)
        self._seeded = False
        self._seed_lock = threading.Lock()

    def _seed_once(self) -> None:
        """Seed the configurations and match the frames, once per program."""
        if self._seeded:
            return
        with self._seed_lock:
            if self._seeded:
                return
            from astrometricslib.pipelines.shared.equipment import scan_configurations

            scan_configurations(self._store, self._config, self._targets)
            self._seeded = True

    def scan(self) -> Page[ObservatoryConfiguration]:
        """Create the configurations the settings and frames imply.

        Adds a configuration for each camera-and-optic pairing in the
        settings, and for each group of frames with one camera and focal
        length, that no stored configuration covers; then points every frame
        at its configuration. Nothing stored is changed, so a second scan
        finds nothing new. The first call of any method of this object scans
        once by itself; call this again after the settings or the frames
        changed.

        Owner: astrometricslib.pipelines.shared.equipment.scan_configurations

        Returns
        -------
        created : `Page` [`ObservatoryConfiguration`]
            The configurations this scan created.
        """
        from astrometricslib.pipelines.shared.equipment import scan_configurations

        with self._seed_lock:
            created = scan_configurations(self._store, self._config, self._targets)
            self._seeded = True
        return created

    def get(self, configuration_id: str) -> ObservatoryConfiguration | None:
        """Read one stored configuration.

        Owner: astrometricslib.pipelines.shared.equipment.get_configuration

        Parameters
        ----------
        configuration_id : `str`
            The configuration's id.

        Returns
        -------
        configuration : `ObservatoryConfiguration` or `None`
            The configuration, or `None` when no configuration has that id.
        """
        from astrometricslib.pipelines.shared.equipment import get_configuration

        self._seed_once()
        return get_configuration(self._store, configuration_id)

    def query(
        self,
        *,
        camera: Camera | None = None,
        telescope: Telescope | None = None,
        time: date | datetime | str | None = None,
        text: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[ObservatoryConfiguration]:
        """List the stored configurations.

        Owner: astrometricslib.pipelines.shared.equipment.query_configurations

        Parameters
        ----------
        camera : `Camera`, optional
            Only configurations with this main camera (any spelling the
            camera profiles know).
        telescope : `Telescope`, optional
            Only configurations with a telescope of this name.
        time : `date`, `datetime` or `str`, optional
            Only configurations in use on this date, such as
            ``"2026-10-03"``.
        text : `str`, optional
            Only configurations whose name, camera-and-focal-length key or
            notes contain this text, ignoring case.
        limit : `int` or `None`, optional
            Most configurations to return. `None` (the default) returns
            every match.
        offset : `int`, optional
            How many matches to skip first. Defaults to 0.

        Returns
        -------
        page : `Page` [`ObservatoryConfiguration`]
            The matches (``items``, ``total``, ``next_offset``), oldest
            start date first.

        Notes
        -----
        Raises `InvalidArgumentError` if `limit` is below 1, `offset` is
        below 0 or `time` is not a date.
        """
        from astrometricslib.pipelines.shared.equipment import query_configurations

        self._seed_once()
        return query_configurations(
            self._store,
            camera=camera,
            telescope=telescope,
            time=time,
            text=text,
            limit=limit,
            offset=offset,
            config=self._config,
        )

    def create(self, configuration: ObservatoryConfiguration) -> ObservatoryConfiguration:
        """Store a new configuration: the equipment changed.

        Owner: astrometricslib.pipelines.shared.equipment.create_configuration

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            The new configuration. Leave `id` empty to get a new one; an
            empty name becomes the camera-and-focal-length key.

        Returns
        -------
        created : `ObservatoryConfiguration`
            The stored configuration, with its id and revision 1.

        Notes
        -----
        Raises `ConflictError` if a configuration with that id is already
        stored.
        """
        from astrometricslib.pipelines.shared.equipment import create_configuration

        self._seed_once()
        return create_configuration(self._store, configuration)

    def update(
        self, configuration: ObservatoryConfiguration, **changes: str | date | None
    ) -> ObservatoryConfiguration:
        """Change a stored configuration's name, notes or dates.

        Owner: astrometricslib.pipelines.shared.equipment.update_configuration

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            The configuration as you read it. Its revision must still be the
            stored one.
        **changes
            New values for any of ``name``, ``notes``, ``valid_from`` and
            ``valid_until``.

        Returns
        -------
        updated : `ObservatoryConfiguration`
            The saved configuration.

        Notes
        -----
        Raises `InvalidArgumentError` if a change names any other field (new
        equipment is a new configuration: use `create`), `NotFoundError` if
        the configuration is not stored, and `ConflictError` if it was
        changed since you read it.
        """
        from astrometricslib.pipelines.shared.equipment import update_configuration

        return update_configuration(self._store, configuration, **changes)


class Observatories(MovedNames):
    """The observatory's equipment.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    targets : `TargetCatalog`
        The targets, whose frames are matched to their configurations.

    Attributes
    ----------
    configurations : `ObservatoryConfigurations`
        Every set of equipment used together, past and present.
    """

    def __init__(self, config: AppConfiguration, *, targets: TargetCatalog) -> None:
        self.configurations = ObservatoryConfigurations(config, targets=targets)
