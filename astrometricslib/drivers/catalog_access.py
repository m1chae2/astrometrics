"""Data saving and loading tools.

This file defines how the program interacts with the database and files,
following a standard pattern so different parts of the code don't have to
worry about where the data actually lives.
"""

import logging
import math
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from astrometricslib.utilities.enums import FilterType
from datastore.butler import Butler as _GenericButler
from datastore.butler import DatasetSpec

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "POSITION_ONLY_STAR_ID_PREFIX",
    "AbstractCatalogAccess",
    "CatalogAccess",
    "FrameSelector",
    "StarPosition",
    "StarSummary",
]

logger = logging.getLogger(__name__)


class FrameSelector(BaseModel):
    """The fields that say which frames are meant.

    These are the properties used to pick frames out of the
    library by hand -- which target, which role, which camera -- so the
    code asking for data never has to know where that data is stored.

    Despite selecting frames of the sky, none of these fields is a sky
    position: `target` names an object, not a right ascension and
    declination.

    Attributes
    ----------
    target : `str`, optional
        Astronomical target name, e.g. "M 81". Default `None`.
    role : `str`, optional
        Frame role, e.g. LIGHT, DARK, FLAT, BIAS. Default `None`.
    camera : `str`, optional
        Camera sensor identifier. Default `None`.
    telescope : `str`, optional
        Telescope optics identifier. Default `None`.
    filter : `FilterType`, optional
        Filter type enum. Default `None`.
    exposure : `float`, optional
        Exposure duration in seconds. Default `None`.
    iso : `str`, optional
        ISO or gain setting. Default `None`.
    sequence_id : `int`, optional
        Frame sequence index. Default `None`.
    path : `str`, optional
        Optional physical path override. Default `None`.
    """

    target: str | None = Field(default=None, description="Astronomical target name, e.g. M 81")
    role: str | None = Field(default=None, description="Frame role, e.g. LIGHT, DARK, FLAT, BIAS")
    camera: str | None = Field(default=None, description="Camera sensor identifier")
    telescope: str | None = Field(default=None, description="Telescope optics identifier")
    filter: FilterType | None = Field(default=None, description="Filter type enum")
    exposure: float | None = Field(default=None, description="Exposure duration in seconds")
    iso: str | None = Field(default=None, description="ISO or Gain setting")
    sequence_id: int | None = Field(default=None, description="Frame sequence index")
    path: str | None = Field(default=None, description="Optional physical path override")


POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"
"""Marks a star known only by where it sits, with no catalog identity.

When plate solving finds a star that no catalog can name, the pipeline
mints an id out of the star's own measured position (see Step 3 of
`pipelines/astrometry/star_identifier.py`), so the id begins with this
prefix. Two solves of the same physical star scatter by a fraction of an
arcsecond and therefore mint two different ids, which is why these stars
are the ones that need matching by position rather than by name.
"""


class StarSummary(BaseModel):
    """The handful of facts about a star that a listing needs.

    A full `StellarObject` carries every measurement ever made of a star
    -- its spectra, its light curve, every identification attempt. A
    list of stars to scroll through needs almost none of that, and
    reading it all back for a catalog of a quarter million stars is slow
    enough to see. This is what a listing actually reads instead.
    """

    id: str
    name: str = ""
    right_ascension: float | None = None
    declination: float | None = None
    target_ids: list[str] = Field(default_factory=list)
    has_spectra: bool = False
    has_photometry: bool = False
    # Filled in by `list_star_summaries` and `list_stars_in_region`. A star
    # with no known brightness or spectral type keeps the defaults.
    magnitude: float | None = None
    spectral_type: str = ""


class StarPosition(BaseModel):
    """Where one star sits on the sky, and which targets it belongs to.

    Used when the only question is "what has already been recorded near
    this spot?", which needs coordinates and an id and nothing else.
    """

    id: str
    right_ascension: float
    declination: float
    target_ids: list[str] = Field(default_factory=list)


# A circle on the sky is searched by first cutting out a box that surely
# contains it, using the database's sorted columns, and then checking each
# star in the box against the circle. The box is made a hair larger than the
# circle so a star sitting exactly on the edge is never lost to rounding.
# 1e-6 degrees is about 0.004 arcseconds, far below any position we store.
_BOX_MARGIN_DEGREES = 1e-6

_FULL_CIRCLE_DEGREES = 360.0


def _region_search_box(
    ra_degrees: float, dec_degrees: float, radius_degrees: float
) -> tuple[tuple[float, float], list[tuple[float, float]] | None]:
    """Work out a box on the sky that contains a circle.

    Declination (up/down) is easy: the box is the circle's center plus and
    minus its radius. Right ascension (left/right) is harder for two
    reasons. Lines of equal right ascension squeeze together near the
    poles, so a circle there spans more right ascension than its radius.
    And right ascension wraps around from 360 back to 0, so a circle near
    that seam needs two ranges.

    Parameters
    ----------
    ra_degrees : `float`
        Right ascension of the circle's center, in degrees.
    dec_degrees : `float`
        Declination of the circle's center, in degrees.
    radius_degrees : `float`
        Radius of the circle, in degrees.

    Returns
    -------
    dec_range : `tuple` [`float`, `float`]
        Lowest and highest declination in the box.
    ra_ranges : `list` [`tuple` [`float`, `float`]] or `None`
        Right ascension ranges of the box, one or two of them. `None`
        means the circle reaches a pole or is so wide that every right
        ascension must be searched.
    """
    radius = min(radius_degrees, 180.0) + _BOX_MARGIN_DEGREES
    dec_range = (max(dec_degrees - radius, -90.0), min(dec_degrees + radius, 90.0))
    if dec_degrees + radius >= 90.0 or dec_degrees - radius <= -90.0:
        return dec_range, None

    # For a circle that does not reach a pole, the widest it gets in right
    # ascension is asin(sin(radius) / cos(dec)). The formula only works
    # while that ratio is below 1, which the pole check above guarantees.
    half_width_degrees = math.degrees(
        math.asin(math.sin(math.radians(radius)) / math.cos(math.radians(dec_degrees)))
    )
    if half_width_degrees >= 180.0:
        return dec_range, None

    ra_low = ra_degrees - half_width_degrees
    ra_high = ra_degrees + half_width_degrees
    if ra_low < 0.0:
        return dec_range, [(ra_low + _FULL_CIRCLE_DEGREES, _FULL_CIRCLE_DEGREES), (0.0, ra_high)]
    if ra_high >= _FULL_CIRCLE_DEGREES:
        return dec_range, [(ra_low, _FULL_CIRCLE_DEGREES), (0.0, ra_high - _FULL_CIRCLE_DEGREES)]
    return dec_range, [(ra_low, ra_high)]


def _angular_separation_degrees(
    ra_degrees: float, dec_degrees: float, other_ra_degrees: np.ndarray, other_dec_degrees: np.ndarray
) -> np.ndarray:
    """Measure the angle on the sky from one point to many points.

    Uses the haversine formula, which stays accurate for very small angles
    where the simpler cosine formula loses digits.

    Parameters
    ----------
    ra_degrees, dec_degrees : `float`
        The single point, in degrees.
    other_ra_degrees, other_dec_degrees : `numpy.ndarray`
        The many points, in degrees.

    Returns
    -------
    separation_degrees : `numpy.ndarray`
        The angle from the single point to each of the many points.
    """
    ra_radians = math.radians(ra_degrees)
    dec_radians = math.radians(dec_degrees)
    other_ra_radians = np.radians(other_ra_degrees)
    other_dec_radians = np.radians(other_dec_degrees)
    half_dec_difference = np.sin((other_dec_radians - dec_radians) / 2.0)
    half_ra_difference = np.sin((other_ra_radians - ra_radians) / 2.0)
    haversine = (
        half_dec_difference**2 + math.cos(dec_radians) * np.cos(other_dec_radians) * half_ra_difference**2
    )
    return np.degrees(2.0 * np.arcsin(np.sqrt(np.clip(haversine, 0.0, 1.0))))


def _split_target_ids(joined_target_ids: Any) -> list[str]:
    """Split the stored, comma-joined target ids back into a list.

    A star can belong to more than one target, so its targets are kept
    as one comma-joined string. Target ids themselves often contain a
    space ("M 13"), so only the ends of each piece are trimmed.

    Returns
    -------
    target_ids : `list` [`str`]
        One id per target, empty if the star belongs to none.
    """
    if not joined_target_ids:
        return []
    return [piece.strip() for piece in str(joined_target_ids).split(",") if piece.strip()]


class AbstractCatalogAccess(ABC):
    """The blueprint for how data is loaded and saved."""

    @abstractmethod
    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Load a specific piece of data.

        Parameters
        ----------
        dataset_type : `str`
            What kind of data to load (like "target_catalog" or "raw_frame").
        selector : `dict`
            Information to help find the exact piece of data.

        Returns
        -------
        dataset : `Any`
            The loaded data.
        """
        pass

    @abstractmethod
    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Save a piece of data.

        Parameters
        ----------
        obj : `Any`
            The data to save.
        dataset_type : `str`
            What kind of data this is.
        selector : `dict`
            Information to help store the data in the right place.
        """
        pass

    @abstractmethod
    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Check if a specific piece of data exists without loading it.

        Parameters
        ----------
        dataset_type : `str`
            What kind of data to check for.
        selector : `dict`
            Information identifying the data.

        Returns
        -------
        exists : `bool`
            True if the data exists, False if not.
        """
        pass

    @abstractmethod
    def get_local_path(self, dataset_type: str, selector: dict[str, Any]) -> str:
        """Get the actual file path on the hard drive for this data.

        Parameters
        ----------
        dataset_type : `str`
            What kind of data to find the path for.
        selector : `dict`
            Information identifying the data.

        Returns
        -------
        path : `str`
            The full file path.
        """
        pass

    @abstractmethod
    def list_star_summaries(
        self, target_id: str | None = None, limit: int | None = None
    ) -> list[StarSummary]:
        """List stars in short form, without loading their full records.

        Parameters
        ----------
        target_id : `str`, optional
            Only stars belonging to this target. Every star when
            omitted.
        limit : `int`, optional
            At most this many stars. Every match when omitted.

        Returns
        -------
        summaries : `list` [`StarSummary`]
            One summary per matching star.
        """
        pass

    @abstractmethod
    def list_stars_in_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
        magnitude_range: tuple[float, float] | None = None,
    ) -> list[StarSummary]:
        """List the stars inside a circle on the sky, in short form.

        Parameters
        ----------
        ra_degrees : `float`
            Right ascension of the circle's center, in degrees.
        dec_degrees : `float`
            Declination of the circle's center, in degrees.
        radius_degrees : `float`
            Radius of the circle, in degrees.
        magnitude_range : `tuple` [`float`, `float`], optional
            Lowest and highest magnitude to keep, ends included. Stars
            with no saved magnitude are left out. Every star is kept when
            omitted.

        Returns
        -------
        summaries : `list` [`StarSummary`]
            One summary, with brightness and spectral type filled in, per
            star whose position is inside the circle.
        """
        pass

    @abstractmethod
    def list_position_only_stars(self, target_id: str | None = None) -> list[StarPosition]:
        """List the stars known only by position, with their coordinates.

        Only stars whose id carries `POSITION_ONLY_STAR_ID_PREFIX` and
        that have usable coordinates are returned -- the ones that can
        be matched to each other by position.

        Parameters
        ----------
        target_id : `str`, optional
            Only stars belonging to this target. Every star when
            omitted.

        Returns
        -------
        positions : `list` [`StarPosition`]
            One entry per position-only star.
        """
        pass

    # The four methods below are ordinary (not abstract) so that a simple
    # stand-in for a database, such as the ones tests use, keeps working
    # without writing them. These versions read the whole catalog through
    # `get` and so are slow on a real library; `CatalogAccess` replaces every
    # one of them with a query that touches only the rows it needs.

    def get_by_ids(self, dataset_type: str, ids: list[str]) -> list[Any]:
        """Load only the records with the given ids.

        Parameters
        ----------
        dataset_type : `str`
            The kind of data to load (like "stellar_catalog").
        ids : `list` [`str`]
            The ids to look for.

        Returns
        -------
        records : `list`
            The records that were found, in no particular order.
        """
        wanted_ids = set(ids)
        return [record for record in self.get(dataset_type, {}) if record.id in wanted_ids]

    def list_star_ids(self) -> list[str]:
        """List the id of every star in the catalog.

        Returns
        -------
        star_ids : `list` [`str`]
            One id per star.
        """
        return [summary.id for summary in self.list_star_summaries()]

    def existing_star_ids(self, ids: list[str]) -> set[str]:
        """Say which of the given star ids are in the catalog.

        Parameters
        ----------
        ids : `list` [`str`]
            The star ids to look for.

        Returns
        -------
        found_ids : `set` [`str`]
            The subset of `ids` that has a star record.
        """
        return set(ids) & set(self.list_star_ids())

    def find_star_ids_by_name(self, name: str) -> list[str]:
        """Find the ids of stars whose id or name equals `name`, ignoring case.

        Parameters
        ----------
        name : `str`
            The id or name to look for.

        Returns
        -------
        star_ids : `list` [`str`]
            The ids of every matching star.
        """
        if not name:
            return []
        wanted_name = name.lower()
        return [
            summary.id
            for summary in self.list_star_summaries()
            if summary.id == name or (summary.name and summary.name.lower() == wanted_name)
        ]


def _target_extra_columns(target: Any) -> dict[str, Any]:
    """Pull specific columns from a target so it can be searched quickly.

    Returns
    -------
    columns : `dict`
        A dictionary containing the target's name and coordinates.
    """
    return {"name": target.common_name, "ra": target.ra, "dec": target.dec}


def _coerce_float(value: Any) -> float | None:
    """Try to convert a value into a floating-point number.

    Returns
    -------
    coerced : `float` or `None`
        The number, or None if it couldn't be converted.
    """
    if value in ("", None):
        return None
    try:
        return float(value)
    except ValueError, TypeError:
        return None


def _stellar_extra_columns(stellar_object: Any) -> dict[str, Any]:
    """Pull out specific information from a star so it can be searched quickly.

    This helps us quickly find if a star has specific data without
    having to load the entire star object.

    Returns
    -------
    columns : `dict`
        A dictionary containing basic information about the star.
    """
    target_ids = getattr(stellar_object, "target_ids", None)
    return {
        "target_id": ",".join(target_ids) if target_ids else None,
        "name": stellar_object.name,
        "ra": _coerce_float(stellar_object.right_ascension),
        "dec": _coerce_float(stellar_object.declination),
        "magnitude": _coerce_float(getattr(stellar_object, "magnitude", None)),
        "has_spectra": int(stellar_object.has_spectra),
        "has_photometry": int(stellar_object.has_photometry),
        "spectral_type": stellar_object.spectral_type or "",
    }


class CatalogAccess(AbstractCatalogAccess):
    """Loads and saves data using this computer's own disk.

    Handles both database records (like the star catalog) and image
    files (like FITS frames). Code that asks for data goes through here
    and never has to know which of the two it is getting, or where on
    disk it sits.
    """

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Set up the CatalogAccess.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application settings. If not provided, it will use the
            default settings.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()
        self.config = config
        self._generic = self._build_generic_butler(config)

    @staticmethod
    def _build_generic_butler(config: Any) -> _GenericButler:
        from astrometricslib.models.stellar_source import StellarObject
        from astrometricslib.models.target import Target

        return _GenericButler(
            config,
            db_name="astrometrics.db",
            specs={
                "target_catalog": DatasetSpec(
                    table_name="targets",
                    model_class=Target,
                    extra_column_types={"name": "TEXT", "ra": "TEXT", "dec": "TEXT"},
                    extra_columns=_target_extra_columns,
                ),
                "stellar_catalog": DatasetSpec(
                    table_name="stellar_objects",
                    model_class=StellarObject,
                    extra_column_types={
                        "target_id": "TEXT",
                        "name": "TEXT",
                        "ra": "REAL",
                        "dec": "REAL",
                        "magnitude": "REAL",
                        "has_spectra": "INTEGER",
                        "has_photometry": "INTEGER",
                        "spectral_type": "TEXT",
                    },
                    extra_columns=_stellar_extra_columns,
                    # Rows saved before this column existed still hold the
                    # value inside their stored JSON. The database copies it
                    # out once, when the column is first added.
                    column_backfills={
                        "spectral_type": "COALESCE(json_extract(data_json, '$.spectralType'), '')"
                    },
                    # The first entry speeds up an exact single-target
                    # match (the common case: most stars belong to only
                    # one target). The
                    # two places this column is actually filtered today
                    # -- list_star_summaries and list_position_only_stars
                    # -- use `like` (a star can belong to more than one
                    # target, comma-joined, so a substring match is the
                    # only safe SQL prefilter) rather than exact
                    # equality; a leading-wildcard LIKE cannot seek this
                    # B-tree index and falls back to a full scan
                    # regardless. Kept anyway since it costs little at
                    # this table's size and does serve an exact match, if
                    # a future caller adds one.
                    #
                    # The second entry is one index over every column that
                    # `list_stars_in_region` reads, starting with "dec". A
                    # sky map only wants the stars near one spot. Sorting
                    # by declination lets the database skip the stars that
                    # are too far north or south, and holding every column
                    # the map reads means it never opens the large stored
                    # rows. Measured on a real 270,000-star library, a
                    # region read took about 136 ms with a plain "dec"
                    # index and about 8 ms with this one, at a cost of
                    # about 30 MB of disk.
                    indexed_columns=(
                        "target_id",
                        (
                            "dec",
                            "ra",
                            "magnitude",
                            "has_spectra",
                            "has_photometry",
                            "spectral_type",
                            "name",
                            "target_id",
                            "id",
                        ),
                    ),
                ),
            },
        )

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Load data from the database or hard drive.

        Parameters
        ----------
        dataset_type : `str`
            What to load. Options include "target_catalog", "stellar_catalog",
            "raw_frame", or "stacked_image".
        selector : `dict`
            Information identifying exactly what to load.

        Returns
        -------
        dataset : `Any`
            The requested data.

        Raises
        ------
        ValueError
            If a data type it doesn't recognize is requested.
        """
        if dataset_type == "target_catalog":
            # Goes through local_database.load_targets, which owns the
            # targets table's read path (id/name/ra/dec/data_json
            # schema) directly; writes still go through the shared
            # generic Butler below.
            from astrometricslib.drivers import local_database

            return local_database.load_targets(self.config)
        elif dataset_type == "stellar_catalog":
            # Reads every star from the database into new objects each time,
            # and keeps none of them. It used to keep the list in a cache
            # here, but every write emptied that cache, each `CatalogAccess`
            # had its own, and `put` filled it with whatever list it was
            # handed -- so a busy backend held several full copies (about
            # 2.8 GB each on a 274,000-star library) and was killed for
            # running out of memory. The database is the one copy of the
            # catalog. Ask for only what is needed: `list_star_summaries`,
            # `list_stars_in_region`, `get_by_ids`, `list_star_ids` and
            # `existing_star_ids`.
            return self._generic.get_all("stellar_catalog")

        elif dataset_type == "raw_frame" or dataset_type == "stacked_image":
            path = self.get_local_path(dataset_type, selector)
            from astrometricslib.drivers.image import AstrometricsImage

            return AstrometricsImage(path)
        else:
            raise ValueError(f"Unknown dataset type: {dataset_type}")

    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Save data to the database or hard drive.

        Parameters
        ----------
        obj : `Any`
            The data to save.
        dataset_type : `str`
            What kind of data this is (e.g., "target_catalog").
        selector : `dict`
            Information on where to store the data (currently unused here).

        Raises
        ------
        ValueError
            If an unsupported data type is being saved.
        """
        if dataset_type == "target_catalog":
            self._generic.put_all("target_catalog", obj)
        elif dataset_type == "target_record":
            self._generic.put(obj, "target_catalog")
        elif dataset_type == "stellar_catalog":
            self._generic.put_all("stellar_catalog", obj)
        else:
            raise ValueError(f"Write operation not supported on dataset type: {dataset_type}")

    def merge_and_record(
        self,
        dataset_type: str,
        objects: list[Any],
        merge_function: Callable[[Any | None, Any], Any],
    ) -> None:
        """Update existing records safely without overwriting other changes.

        This loads the current data, applies your changes using a
        `merge_function`, and saves it back. It locks the database so
        two programs don't accidentally overwrite each other.

        Parameters
        ----------
        dataset_type : `str`
            The kind of data to update (e.g., "stellar_catalog").
        objects : `list`
            The new data to add or update.
        merge_function : `Callable`
            A function that knows how to combine the old data with the new
            data.

        Raises
        ------
        ValueError
            If the dataset type isn't supported for merging.
        """
        if dataset_type not in ("stellar_catalog", "target_catalog"):
            raise ValueError(f"merge_and_record is not supported for dataset type: {dataset_type}")

        self._generic.merge_and_record(dataset_type, objects, merge_function)

    def delete_by_ids(self, dataset_type: str, ids: list[str]) -> None:
        """Delete specific records from the database using their IDs.

        Parameters
        ----------
        dataset_type : `str`
            The kind of data to delete from.
        ids : `list` of `str`
            The specific IDs to remove.

        Raises
        ------
        ValueError
            If the dataset type isn't supported for deleting.
        """
        if dataset_type not in ("stellar_catalog", "target_catalog"):
            raise ValueError(f"delete_by_ids is not supported for dataset type: {dataset_type}")

        self._generic.delete_by_ids(dataset_type, ids)

    def get_by_ids(self, dataset_type: str, ids: list[str]) -> list[Any]:
        """Load only specific records from the database instead of everything.

        Parameters
        ----------
        dataset_type : `str`
            The kind of data to load.
        ids : `list` of `str`
            The specific IDs to look for.

        Returns
        -------
        rows : `list`
            The records that were found.
        """
        return self._generic.get_by_ids(dataset_type, ids)

    def list_star_ids(self) -> list[str]:
        """List the id of every star, reading nothing but the id column.

        Returns
        -------
        star_ids : `list` [`str`]
            One id per star.
        """
        return [row["id"] for row in self._generic.list_projected("stellar_catalog", ["id"])]

    def existing_star_ids(self, ids: list[str]) -> set[str]:
        """Say which of the given star ids are in the catalog.

        Parameters
        ----------
        ids : `list` [`str`]
            The star ids to look for.

        Returns
        -------
        found_ids : `set` [`str`]
            The subset of `ids` that has a star record.
        """
        return self._generic.existing_ids("stellar_catalog", ids)

    def find_star_ids_by_name(self, name: str) -> list[str]:
        """Find the ids of stars whose id or name equals `name`, ignoring case.

        The database narrows the search to names that contain `name`, and
        the exact comparison happens afterwards, so only a handful of rows
        are ever read.

        Parameters
        ----------
        name : `str`
            The id or name to look for.

        Returns
        -------
        star_ids : `list` [`str`]
            The ids of every matching star.
        """
        if not name:
            return []
        wanted_name = name.lower()
        candidate_rows = self._generic.list_projected("stellar_catalog", ["id", "name"], like={"name": name})
        matching_ids = [
            row["id"] for row in candidate_rows if row["name"] and row["name"].lower() == wanted_name
        ]
        # A star matches by its id even when its stored name differs.
        matching_ids.extend(
            found_id for found_id in self.existing_star_ids([name]) if found_id not in matching_ids
        )
        return matching_ids

    def list_star_summaries(
        self, target_id: str | None = None, limit: int | None = None
    ) -> list[StarSummary]:
        """List stars in short form, without loading their full records.

        Reads only the indexed columns, so a star's stored JSON is never
        parsed. On a real 270,450-star catalog that is the difference
        between roughly 0.7 seconds and 26 seconds.

        Parameters
        ----------
        target_id : `str`, optional
            Only stars belonging to this target. Every star when
            omitted.
        limit : `int`, optional
            At most this many stars. Every match when omitted. Because
            the database narrows by substring and the exact check
            happens afterwards, a limited request can return slightly
            fewer stars than asked for.

        Returns
        -------
        summaries : `list` [`StarSummary`]
            One summary per matching star.
        """
        rows = self._generic.list_projected(
            "stellar_catalog",
            [
                "id",
                "name",
                "ra",
                "dec",
                "target_id",
                "has_spectra",
                "has_photometry",
                "magnitude",
                "spectral_type",
            ],
            like={"target_id": target_id} if target_id else None,
            limit=limit,
        )
        summaries = []
        for row in rows:
            target_ids = _split_target_ids(row["target_id"])
            if target_id and target_id not in target_ids:
                continue
            summaries.append(
                StarSummary(
                    id=row["id"],
                    name=row["name"] or "",
                    right_ascension=row["ra"],
                    declination=row["dec"],
                    target_ids=target_ids,
                    has_spectra=bool(row["has_spectra"]),
                    has_photometry=bool(row["has_photometry"]),
                    magnitude=_coerce_float(row["magnitude"]),
                    spectral_type=row["spectral_type"] or "",
                )
            )
        return summaries

    def list_stars_in_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
        magnitude_range: tuple[float, float] | None = None,
    ) -> list[StarSummary]:
        """List the stars inside a circle on the sky, in short form.

        Reads only the saved columns, so a star's stored JSON is never
        parsed, and uses the declination index so stars far from the
        circle are never read at all. Loading every star in full for each
        pan or zoom of the sky map took about ten seconds on a real
        270,000-star library.

        Parameters
        ----------
        ra_degrees : `float`
            Right ascension of the circle's center, in degrees.
        dec_degrees : `float`
            Declination of the circle's center, in degrees.
        radius_degrees : `float`
            Radius of the circle, in degrees.
        magnitude_range : `tuple` [`float`, `float`], optional
            Lowest and highest magnitude to keep, ends included. Stars
            with no saved magnitude are left out. Every star is kept when
            omitted. The database applies this while it searches, so a
            wide view that only wants bright stars never reads the faint
            ones at all.

        Returns
        -------
        summaries : `list` [`StarSummary`]
            One summary, with brightness and spectral type filled in, per
            star whose position is inside the circle. Stars with no
            position are left out. A star exactly at right ascension 0 and
            declination 0 is a "no position" placeholder and is left out
            too, the same as the sky map has always done.
        """
        dec_range, ra_ranges = _region_search_box(ra_degrees, dec_degrees, radius_degrees)
        columns = [
            "id",
            "name",
            "ra",
            "dec",
            "target_id",
            "has_spectra",
            "has_photometry",
            "magnitude",
            "spectral_type",
        ]
        rows: list[dict[str, Any]] = []
        for ra_range in ra_ranges if ra_ranges is not None else [None]:
            between = {"dec": dec_range}
            if ra_range is not None:
                between["ra"] = ra_range
            if magnitude_range is not None:
                between["magnitude"] = magnitude_range
            rows.extend(self._generic.list_projected("stellar_catalog", columns, between=between))
        if not rows:
            return []

        star_ras = np.array([row["ra"] for row in rows], dtype=float)
        star_decs = np.array([row["dec"] for row in rows], dtype=float)
        separations = _angular_separation_degrees(ra_degrees, dec_degrees, star_ras, star_decs)
        is_inside_circle = separations <= radius_degrees

        summaries = []
        for row, star_ra, star_dec, is_inside in zip(
            rows, star_ras, star_decs, is_inside_circle, strict=True
        ):
            # Both coordinates being zero means "no position saved yet".
            has_no_position = not (star_ra or star_dec)
            if not is_inside or has_no_position:
                continue
            summaries.append(
                StarSummary(
                    id=row["id"],
                    name=row["name"] or "",
                    right_ascension=float(star_ra),
                    declination=float(star_dec),
                    target_ids=_split_target_ids(row["target_id"]),
                    has_spectra=bool(row["has_spectra"]),
                    has_photometry=bool(row["has_photometry"]),
                    magnitude=row["magnitude"],
                    spectral_type=row["spectral_type"] or "",
                )
            )
        return summaries

    def list_position_only_stars(self, target_id: str | None = None) -> list[StarPosition]:
        """List the stars known only by position, with their coordinates.

        Parameters
        ----------
        target_id : `str`, optional
            Only stars belonging to this target. Every star when
            omitted.

        Returns
        -------
        positions : `list` [`StarPosition`]
            One entry per position-only star that has usable
            coordinates.
        """
        rows = self._generic.list_projected(
            "stellar_catalog",
            ["id", "ra", "dec", "target_id"],
            like={"target_id": target_id} if target_id else None,
        )
        positions = []
        for row in rows:
            if not row["id"].startswith(POSITION_ONLY_STAR_ID_PREFIX):
                continue
            if row["ra"] is None or row["dec"] is None:
                continue
            target_ids = _split_target_ids(row["target_id"])
            if target_id and target_id not in target_ids:
                continue
            positions.append(
                StarPosition(
                    id=row["id"],
                    right_ascension=row["ra"],
                    declination=row["dec"],
                    target_ids=target_ids,
                )
            )
        return positions

    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Check if a file exists on the hard drive.

        Parameters
        ----------
        dataset_type : `str`
            What kind of file it is.
        selector : `dict`
            Information to build the file path.

        Returns
        -------
        exists : `bool`
            True if the file is found, False otherwise.
        """
        try:
            path = self.get_local_path(dataset_type, selector)
            return os.path.exists(path)
        except Exception:
            return False

    def get_local_path(self, dataset_type: str, selector: dict[str, Any]) -> str:
        """Figure out where a file should be saved on the hard drive.

        Parameters
        ----------
        dataset_type : `str`
            What kind of file (e.g., "stacked_image" or "raw_frame").
        selector : `dict`
            Information about the target and what kind of frame it is.

        Returns
        -------
        path : `str`
            The full file path.

        Raises
        ------
        ValueError
            If it doesn't know how to build a path for that data type.
        """
        if selector.get("path"):
            return selector["path"]

        target = selector.get("target", "Unknown")
        role = selector.get("role", "LIGHT").upper()

        if dataset_type == "stacked_image":
            safe_target = target.replace(" ", "_")
            return os.path.join(
                self.config.get_frames_path(), "lights", target, f"{safe_target}_Stacked.fits"
            )
        elif dataset_type == "raw_frame":
            # Just fallback to frames root lights mapping
            return os.path.join(self.config.get_frames_path(), role.lower() + "s", target)
        else:
            raise ValueError(f"Local path resolution not supported for dataset type: {dataset_type}")
