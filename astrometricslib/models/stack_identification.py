"""What `Processing.identify_stack` returns: the named stars of one stack.

Each star the stack's own star finder detected gets a name: the most
familiar catalog designation (a proper name, a Bayer or Flamsteed name, an HD
or HIP number, then survey numbers, then ``Gaia DR3 <id>``), or
``Unnamed star`` when no catalog star is close. Each named star also carries
the id the library keeps its record under, so a viewer needs no guessing to
find the record. Gaia stars bright enough to be seen but with no
detection are listed too, so a viewer can show what the finder missed.
Nothing here is stored in the target's star catalog. Every number is a plain
Python float, int, bool or string, so it can be sent to the app as it is.
"""

from pydantic import BaseModel

__all__ = ["IdentifiedStar", "StackIdentification"]


class IdentifiedStar(BaseModel):
    """One star of the stack, a detection, a catalog star, or both.

    Parameters
    ----------
    id : `str`
        A key unique within the result.
    name : `str`
        The most familiar designation, or ``Unnamed star``.
    kind : `str`
        ``matched`` (a detection with a catalog star), ``unmatched`` (a
        detection with none) or ``catalog_only`` (a Gaia star bright enough
        to be detected that has no detection).
    x, y : `float`
        The position in 0-based pixels of the stack (the center of the first
        pixel is 0, 0).
    ra_deg, dec_deg : `float`
        The sky position, in degrees: the catalog's (moved to the date of the
        stack) when there is one, else the detection's.
    g_mag : `float` or `None`
        The Gaia G magnitude, when a Gaia star is matched.
    gaia_source_id : `str` or `None`
        The Gaia DR3 source ID, when a Gaia star is matched.
    name_source : `str`
        ``simbad``, ``gaia`` or ``none``: where the name came from.
    aliases : `list` [`str`]
        The familiar other names SIMBAD lists for the star, most familiar
        first, as a library record keeps them.
    library_id : `str` or `None`
        The id the library saves (or will save) the star's record under:
        SIMBAD's record id for a star SIMBAD names, else ``Gaia DR3 <id>``.
        `None` for a star no catalog names. A record exists under it only
        once a run measured the star.
    """

    id: str
    name: str
    kind: str
    x: float
    y: float
    ra_deg: float
    dec_deg: float
    g_mag: float | None = None
    gaia_source_id: str | None = None
    name_source: str = "none"
    aliases: list[str] = []
    library_id: str | None = None


class StackIdentification(BaseModel):
    """The named stars of one stack and how they were found.

    Parameters
    ----------
    path : `str`
        The stack.
    width, height : `int`
        The stack's size in pixels.
    solution_reused : `bool`
        True when the stack's saved plate solution was used; False when it
        was solved by this call.
    stars : `list` [`IdentifiedStar`]
        Every detection, then the catalog stars with no detection.
    matched_count, unmatched_count, catalog_only_count : `int`
        How many stars of each kind.
    gaia_depth : `float`
        The Gaia G magnitude the tiles were read to.
    catalog_only_limit : `float`
        Gaia stars brighter than this G and with no detection are listed as
        ``catalog_only``.
    gaia_tiles_missing : `int`
        Gaia tiles under the stack that were not in the cache to the depth;
        stars there are not named from Gaia.
    simbad_used : `bool`
        True when SIMBAD answered and its names were used.
    notes : `list` [`str`]
        Sentences for the person: why SIMBAD names are missing, missing
        tiles, and so on.
    viewer_offset_x, viewer_offset_y : `int`
        Where the viewer's copy of the stack (its stretched picture) starts
        on the stack, in pixels: 0 when it is the whole stack, the crop's
        corner when it is cropped. Subtract them from ``x`` and ``y`` to place
        a star on that copy.
    viewer_width, viewer_height : `int`
        The size of that copy, in pixels.
    alias_records_updated : `int`
        How many records of the star catalog gained SIMBAD names as aliases
        in this run.
    """

    path: str
    width: int
    height: int
    solution_reused: bool
    stars: list[IdentifiedStar]
    matched_count: int
    unmatched_count: int
    catalog_only_count: int
    gaia_depth: float
    catalog_only_limit: float
    gaia_tiles_missing: int = 0
    simbad_used: bool = False
    notes: list[str] = []
    viewer_offset_x: int = 0
    viewer_offset_y: int = 0
    viewer_width: int = 0
    viewer_height: int = 0
    alias_records_updated: int = 0
