"""Purpose: Make the simulated mount's go-to error scatter in direction.

Description: The INDI telescope simulator has one constant go-to offset (its
``MOUNT_MODEL`` property). A real mount lands a different distance away, in a
different direction, each time it goes to a new place. This module redraws
the offset for every new destination, from a seeded random generator, so the
errors scatter and a run repeats.

The size of the scatter comes from the user's own recorded plate solves (the
fit step, `simulator.fit`): the middle is the median first-attempt offset
and the spread is the median absolute deviation of those offsets, as a
standard deviation. The numbers are in the results file's ``go_to_scatter``
entry, which `GotoScatterSettings.from_dict` reads.

How the INDI simulator behaves (measured live on INDI 2.2.5, 2026-11-28):

* It applies ``MOUNT_MODEL`` when the slew lands, not before. Writing the new
  offset in the first seconds of a slew is early enough.
* It learns a sync. After a sync, the mount lands exactly where it is told
  near that place, and the offset set before the sync has no further effect
  there. The learned sync is applied to the target when the slew is
  commanded, so a new offset has the right size only once the learned syncs
  are cleared (``ALIGNMENT_POINTSET_ACTION`` ``CLEAR``, then
  ``ALIGNMENT_POINTSET_COMMIT``) and the target is sent again. Each new
  destination therefore clears the syncs, sets a fresh offset and repeats the
  same slew command a moment after the app's own.
* A sync does not start a slew (the coordinates property does not go ``Busy``),
  and a re-slew to the destination just centered is the same destination, so
  neither draws a new offset. Only a slew to a place at least
  `NEW_DESTINATION_DEGREES` from the last one does.

The watcher talks to the INDI server only through the two callables it is
given, so a test needs no INDI server.
"""

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from simulator.fit.fitting import ARCSEC_PER_ARCMIN, GO_TO_SCATTER_SEED, MOUNT_MODEL_SIGNS
from simulator.fit.ranges import MOUNT, clamp_to_range

__all__ = [
    "GO_TO_SCATTER_SEED",
    "NEW_DESTINATION_DEGREES",
    "GotoScatter",
    "GotoScatterSettings",
]

#: A slew to a place at least this far (degrees) from the last slew's place is
#: a new destination. A re-slew to the same place after a sync is not.
NEW_DESTINATION_DEGREES = 0.1
#: Seconds between two looks at the mount.
POLL_SECONDS = 0.2
#: The INDI properties read and written (device ``Telescope Simulator``).
TARGET_RA_NAME = f"{MOUNT}.TARGET_EOD_COORD.RA"
TARGET_DEC_NAME = f"{MOUNT}.TARGET_EOD_COORD.DEC"
STATE_NAME = f"{MOUNT}.EQUATORIAL_EOD_COORD._STATE"
CLEAR_SYNCS_SETTING = f"{MOUNT}.ALIGNMENT_POINTSET_ACTION.CLEAR=On"
#: The simulator acts on the action only after it is committed.
COMMIT_SYNCS_SETTING = f"{MOUNT}.ALIGNMENT_POINTSET_COMMIT.ALIGNMENT_POINTSET_COMMIT=On"
#: The most draws kept for a report.
MAXIMUM_REMEMBERED_DRAWS = 1000


@dataclass(frozen=True)
class GotoScatterSettings:
    """The size of the go-to scatter.

    Attributes
    ----------
    mean_ra_arcsec, mean_dec_arcsec : `float`
        The middle of the offset (solved minus commanded), as an angle on
        each axis, in arcseconds: the median of the user's first attempts.
    sigma_ra_arcsec, sigma_dec_arcsec : `float`
        The spread on each axis, in arcseconds (1.4826 times the median
        absolute deviation of the user's first attempts).
    seed : `int`
        The seed of the random generator.
    count : `int`
        How many first attempts the numbers come from.
    """

    mean_ra_arcsec: float
    mean_dec_arcsec: float
    sigma_ra_arcsec: float
    sigma_dec_arcsec: float
    seed: int = GO_TO_SCATTER_SEED
    count: int = 0

    @classmethod
    def from_dict(cls, entry: dict | None) -> GotoScatterSettings | None:
        """Read the ``go_to_scatter`` entry of the results file.

        Parameters
        ----------
        entry : `dict` or `None`
            The entry, or `None` when the file has none.

        Returns
        -------
        settings : `GotoScatterSettings` or `None`
            The settings, or `None` when there is no entry or its numbers are
            not numbers (a status: the offset stays constant).
        """
        if not entry:
            return None
        try:
            return cls(
                mean_ra_arcsec=float(entry["mean_ra_arcsec"]),
                mean_dec_arcsec=float(entry["mean_dec_arcsec"]),
                sigma_ra_arcsec=float(entry["sigma_ra_arcsec"]),
                sigma_dec_arcsec=float(entry["sigma_dec_arcsec"]),
                seed=int(entry.get("seed", GO_TO_SCATTER_SEED)),
                count=int(entry.get("count", 0)),
            )
        except KeyError, TypeError, ValueError:
            return None


def mount_model_terms(ra_arcsec: float, dec_arcsec: float) -> tuple[float, float]:
    """Turn an offset into the ``MM_IH`` and ``MM_ID`` values.

    Parameters
    ----------
    ra_arcsec, dec_arcsec : `float`
        The wanted offset (solved minus commanded), as an angle on each axis,
        in arcseconds.

    Returns
    -------
    ih_arcmin, id_arcmin : `tuple` [`float`, `float`]
        The terms in arcminutes, signed (`MOUNT_MODEL_SIGNS`) and clamped to
        the property's range.
    """
    ih = MOUNT_MODEL_SIGNS["MM_IH"] * ra_arcsec / ARCSEC_PER_ARCMIN
    id_ = MOUNT_MODEL_SIGNS["MM_ID"] * dec_arcsec / ARCSEC_PER_ARCMIN
    return (
        clamp_to_range(MOUNT, "MOUNT_MODEL", "MM_IH", ih)[0],
        clamp_to_range(MOUNT, "MOUNT_MODEL", "MM_ID", id_)[0],
    )


class GotoScatter:
    """Redraw the simulated mount's go-to offset for each new destination.

    Parameters
    ----------
    settings : `GotoScatterSettings`
        The middle and the spread of the offset, and the seed.
    read : `~collections.abc.Callable` [[`str`], `str`]
        Reads one INDI property element (``device.property.element``) and
        gives its value, or an empty text when it is not there.
    write : `~collections.abc.Callable` [..., `None`]
        Sets INDI properties from ``device.property.element=value`` texts.

    Attributes
    ----------
    draws : `list` [`tuple` [`float`, `float`]]
        The offsets drawn so far, in arcseconds (right ascension angle,
        declination), the newest last. The first attempt of a plate-solve run
        should measure the draw made for its destination.
    """

    def __init__(
        self,
        settings: GotoScatterSettings,
        read: Callable[[str], str],
        write: Callable[..., None],
    ) -> None:
        """Keep the settings; nothing is drawn yet."""
        self.settings = settings
        self._read = read
        self._write = write
        self._random = np.random.default_rng(settings.seed)
        self._last_destination: tuple[float, float] | None = None
        self.draws: list[tuple[float, float]] = []
        self.destinations: list[tuple[float, float]] = []

    def draw(self) -> tuple[float, float]:
        """Draw the next offset.

        Returns
        -------
        offset : `tuple` [`float`, `float`]
            Right ascension angle and declination offset in arcseconds: the
            middle plus a normal draw of the spread on each axis.
        """
        settings = self.settings
        offset = (
            float(self._random.normal(settings.mean_ra_arcsec, settings.sigma_ra_arcsec)),
            float(self._random.normal(settings.mean_dec_arcsec, settings.sigma_dec_arcsec)),
        )
        self.draws.append(offset)
        del self.draws[:-MAXIMUM_REMEMBERED_DRAWS]
        return offset

    def apply(self, offset: tuple[float, float], destination: tuple[float, float]) -> None:
        """Clear the learned syncs, write an offset and repeat the slew.

        Parameters
        ----------
        offset : `tuple` [`float`, `float`]
            Right ascension angle and declination offset in arcseconds.
        destination : `tuple` [`float`, `float`]
            The slew's target, right ascension in hours and declination in
            degrees, sent again so the mount works it out without the
            learned syncs.
        """
        ih, id_ = mount_model_terms(*offset)
        self._write(CLEAR_SYNCS_SETTING)
        self._write(COMMIT_SYNCS_SETTING)
        self._write(f"{MOUNT}.MOUNT_MODEL.MM_IH;MM_ID={ih};{id_}")
        self._write(f"{MOUNT}.EQUATORIAL_EOD_COORD.RA;DEC={destination[0]};{destination[1]}")

    def check(self) -> bool:
        """Look at the mount once, and draw an offset for a new destination.

        A slew has started when the mount's coordinates are ``Busy``. Its
        destination is new when it is at least `NEW_DESTINATION_DEGREES` from
        the last slew's.

        Returns
        -------
        redrawn : `bool`
            `True` when a new offset was written.
        """
        if self._read(STATE_NAME) != "Busy":
            return False
        try:
            destination = (float(self._read(TARGET_RA_NAME)), float(self._read(TARGET_DEC_NAME)))
        except ValueError:
            return False
        if not all(math.isfinite(value) for value in destination):
            return False
        if self._last_destination is not None and _separation_degrees(self._last_destination, destination) < (
            NEW_DESTINATION_DEGREES
        ):
            return False
        self._last_destination = destination
        self.destinations.append(destination)
        self.apply(self.draw(), destination)
        return True

    def watch(self, stop_event: threading.Event) -> None:
        """Check the mount again and again until stopped.

        Parameters
        ----------
        stop_event : `threading.Event`
            Set to end the watch.
        """
        while not stop_event.is_set():
            self.check()
            stop_event.wait(POLL_SECONDS)


def _separation_degrees(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Give the small-angle distance between two targets.

    Parameters
    ----------
    first, second : `tuple` [`float`, `float`]
        Right ascension (hours) and declination (degrees).

    Returns
    -------
    separation : `float`
        The distance in degrees.
    """
    middle_dec = math.radians((first[1] + second[1]) / 2.0)
    delta_ra = ((second[0] - first[0] + 12.0) % 24.0 - 12.0) * 15.0 * math.cos(middle_dec)
    return math.hypot(delta_ra, second[1] - first[1])
