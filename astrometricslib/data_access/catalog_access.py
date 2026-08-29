"""Data saving and loading tools.

This file defines how the program interacts with the database and files,
following a standard pattern so different parts of the code don't have to
worry about where the data actually lives.
"""

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

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
    """The fields that say which frames you mean.

    These are the properties you would use to pick frames out of the
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
"""Marks a star we know only by where it sits, with no catalog identity.

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


class StarPosition(BaseModel):
    """Where one star sits on the sky, and which targets it belongs to.

    Used when the only question is "what have we already recorded near
    this spot?", which needs coordinates and an id and nothing else.
    """

    id: str
    right_ascension: float
    declination: float
    target_ids: list[str] = Field(default_factory=list)


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
    """The blueprint for how we load and save data."""

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
        self._stellar_catalog_cache: list[Any] | None = None
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
                    },
                    extra_columns=_stellar_extra_columns,
                    # Speeds up an exact single-target match (the common
                    # case: most stars belong to only one target). The
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
                    indexed_columns=("target_id",),
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
            If you ask for a data type it doesn't recognize.
        """
        if dataset_type == "target_catalog":
            # Goes through local_database.load_targets rather than
            # self._generic.get_all so the one-time legacy JSON-shard
            # migration it performs on first boot still runs; writes
            # still go through the shared generic Butler below.
            from astrometricslib.drivers import local_database

            return local_database.load_targets(self.config)
        elif dataset_type == "stellar_catalog":
            # put()/merge_and_record() write through to (or
            # invalidate) self._stellar_catalog_cache specifically so
            # this can skip disk I/O on repeated reads -- serve from it
            # when populated, instead of unconditionally hitting disk.
            if self._stellar_catalog_cache is None:
                self._stellar_catalog_cache = self._generic.get_all("stellar_catalog")
            return self._stellar_catalog_cache

        elif dataset_type == "raw_frame" or dataset_type == "stacked_image":
            path = self.get_local_path(dataset_type, selector)
            from astrometricslib.image_processing.image import AstrometricsImage

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
            If you try to save a data type it doesn't support.
        """
        if dataset_type == "target_catalog":
            self._generic.put_all("target_catalog", obj)
        elif dataset_type == "target_record":
            self._generic.put(obj, "target_catalog")
        elif dataset_type == "stellar_catalog":
            self._generic.put_all("stellar_catalog", obj)
            self._stellar_catalog_cache = obj
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

        # Invalidate the cache so the next get() re-reads the merged
        # state from disk.
        if dataset_type == "stellar_catalog":
            self._stellar_catalog_cache = None

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

        if dataset_type == "stellar_catalog":
            self._stellar_catalog_cache = None

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
            ["id", "name", "ra", "dec", "target_id", "has_spectra", "has_photometry"],
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
