"""Purpose: Say which capture settings a camera may be asked to use.

Description: A night plan names a gain setting, an offset, a binning and a
filter for each exposure. This module decides whether a requested value is
one the camera can honor, and says why when it is not (register entry
WAY-51).

The allowed gain, offset and binning combinations are the ones the camera
profile lists in `CameraProfile.sensor_noise`, because those are the
settings whose noise has been measured. The profile keeps them as text such
as ``"100"`` or ``"1x1"``; this module turns them into whole numbers.

Two cases need care:

* A camera with a measured profile refuses a combination that was not
  measured, and the reason lists the combinations that were.
* A camera with no measured combination (the generic fallback profile, or a
  profile with an empty list, such as the Nikon) accepts the request and
  marks it "setting not measured". The library cannot tell a good value from
  a bad one there, so it never rejects.

The filters come from the equipment configuration when it lists any, and a
filter outside that list is refused. When it lists none, they are the filters
seen on that camera's frames in the library; a filter not yet seen there is
accepted with a note, because the first use of a new filter is legitimate.
When both are empty, any filter is accepted and the answer says the filter
was not checked.
"""

from collections.abc import Collection, Iterable, Sequence
from types import SimpleNamespace
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import CaptureSetting, ConfigurationError, FilterType, resolve_camera_profile
from wayfindinglib.analytics.capture_history import normalize_name

NOT_MEASURED_NOTE = "setting not measured"
"""The note added when the camera profile has no measured setting."""


class CameraProfileLike(Protocol):
    """The parts of a camera profile this module reads.

    `astrometricslib` returns the profile from `resolve_camera_profile`
    but does not export its class from the package root, and this library
    imports only from the root. A `Protocol` names what is needed.
    """

    camera_name: str
    is_generic_fallback: bool
    sensor_noise: Sequence[Any]


class MeasuredCaptureSetting(BaseModel):
    """One gain, offset and binning combination the camera profile measured."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    gain_setting: int = Field(alias="gainSetting")
    offset: int = Field(alias="offset")
    binning: int = Field(alias="binning")


class CaptureValueCheck(BaseModel):
    """The answer for one requested capture setting.

    Attributes
    ----------
    accepted : `bool`
        Whether the plan may hold this setting.
    setting_measured : `bool` or `None`
        `True` when the combination is one the profile measured. `False`
        when the camera has no measured combination at all, so the request
        is accepted but marked. `None` when the request named none of gain
        setting, offset or binning, so there was nothing to compare.
    reasons : `list` [`str`]
        Why the setting was refused, one sentence each. Empty when accepted.
    notes : `list` [`str`]
        Remarks on an accepted setting, such as "setting not measured".
    """

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool = Field(alias="accepted")
    setting_measured: bool | None = Field(default=None, alias="settingMeasured")
    reasons: list[str] = Field(default_factory=list, alias="reasons")
    notes: list[str] = Field(default_factory=list, alias="notes")


class AllowedCaptureValues(BaseModel):
    """What one camera may be asked to do, and the check for a request.

    Attributes
    ----------
    camera_name : `str`
        The camera the values belong to.
    settings_measured : `bool`
        `True` when the camera profile lists at least one measured
        combination of gain setting, offset and binning.
    measured_settings : `list` [`MeasuredCaptureSetting`]
        The measured combinations. Empty when `settings_measured` is `False`.
    filters : `list` [`str`]
        The filter names the plan may use. Empty when none are known.
    filter_source : `str`
        ``"equipment"``, ``"library"`` or ``"none"``: the source of `filters`.
    """

    model_config = ConfigDict(populate_by_name=True)

    camera_name: str = Field(alias="cameraName")
    settings_measured: bool = Field(alias="settingsMeasured")
    measured_settings: list[MeasuredCaptureSetting] = Field(default_factory=list, alias="measuredSettings")
    filters: list[str] = Field(default_factory=list, alias="filters")
    filter_source: str = Field(default="none", alias="filterSource")

    def check(
        self,
        gain_setting: int | None = None,
        offset: int | None = None,
        binning: int | None = None,
        filter_name: str | FilterType | None = None,
    ) -> CaptureValueCheck:
        """Check one requested gain setting, offset, binning and filter.

        A value left as `None` matches any measured combination, so a
        request that names only the gain setting is checked against the
        measured gain settings.

        Parameters
        ----------
        gain_setting : `int`, optional
            The requested gain setting.
        offset : `int`, optional
            The requested offset.
        binning : `int`, optional
            The requested binning factor.
        filter_name : `str` or `FilterType`, optional
            The requested filter. ``FilterType.NONE`` is always accepted.

        Returns
        -------
        check : `CaptureValueCheck`
            Whether the setting is accepted, and why not or with what note.
        """
        reasons: list[str] = []
        notes: list[str] = []
        setting_measured: bool | None = None
        named = {"gain setting": gain_setting, "offset": offset, "binning": binning}
        if any(value is not None for value in named.values()):
            if not self.settings_measured:
                setting_measured = False
                notes.append(NOT_MEASURED_NOTE)
            else:
                matches = [
                    measured
                    for measured in self.measured_settings
                    if gain_setting in (None, measured.gain_setting)
                    and offset in (None, measured.offset)
                    and binning in (None, measured.binning)
                ]
                setting_measured = bool(matches)
                if not matches:
                    requested = ", ".join(
                        f"{name} {value}" for name, value in named.items() if value is not None
                    )
                    measured_text = "; ".join(
                        f"gain setting {measured.gain_setting}, "
                        f"offset {measured.offset}, binning {measured.binning}"
                        for measured in self.measured_settings
                    )
                    reasons.append(
                        f"{self.camera_name} has no measured setting with {requested}. "
                        f"Measured settings: {measured_text}."
                    )
        if filter_name is not None and filter_name != FilterType.NONE:
            if self.filters:
                if not _filter_is_listed(filter_name, self.filters):
                    text = (
                        f"Filter {_filter_text(filter_name)} is not one of this camera's filters: "
                        f"{', '.join(self.filters)}."
                    )
                    if self.filter_source == "equipment":
                        reasons.append(text)
                    else:
                        # Frames seen so far are not a full list: a filter
                        # used for the first time is noted, not refused.
                        notes.append(text)
            else:
                notes.append("filter not checked: no filters are listed for this camera")
        return CaptureValueCheck(
            accepted=not reasons, setting_measured=setting_measured, reasons=reasons, notes=notes
        )


def _filter_text(filter_name: str | FilterType) -> str:
    """Give a filter as plain text.

    Parameters
    ----------
    filter_name : `str` or `FilterType`
        The filter.

    Returns
    -------
    text : `str`
        The filter's value when it is a `FilterType`, else the text itself.
    """
    return filter_name.value if isinstance(filter_name, FilterType) else str(filter_name)


def _filter_spellings(filter_name: str | FilterType) -> set[str]:
    """List every lower-case spelling a filter goes by.

    FITS headers write "Luminance", "L" or "Lum" for the same filter, so a
    member of `FilterType` counts under both its value and its name.

    Parameters
    ----------
    filter_name : `str` or `FilterType`
        The filter.

    Returns
    -------
    spellings : `set` [`str`]
        The lower-case spellings.
    """
    text = _filter_text(filter_name)
    spellings = {text.strip().casefold()}
    for member in FilterType:
        if member.value.casefold() in spellings or member.name.casefold() in spellings:
            spellings.add(member.value.casefold())
            spellings.add(member.name.casefold())
    return spellings


def filter_names_match(first: str | FilterType, second: str | FilterType) -> bool:
    """Say whether two filter names are the same filter.

    FITS headers write "Luminance", "L" or "Lum" for one filter, so the
    comparison goes through every spelling a filter has.

    Parameters
    ----------
    first, second : `str` or `FilterType`
        The two filter names.

    Returns
    -------
    same : `bool`
        `True` when a spelling of one equals a spelling of the other.
    """
    return bool(_filter_spellings(first) & _filter_spellings(second))


def _filter_is_listed(filter_name: str | FilterType, listed: Collection[str]) -> bool:
    """Say whether a filter matches one of the listed names.

    Parameters
    ----------
    filter_name : `str` or `FilterType`
        The requested filter.
    listed : `Collection` [`str`]
        The names the camera offers.

    Returns
    -------
    listed_ : `bool`
        `True` when a spelling of the filter equals a spelling of a name.
    """
    wanted = _filter_spellings(filter_name)
    return any(wanted & _filter_spellings(name) for name in listed)


def _as_whole_number(text: str) -> int | None:
    """Turn a profile setting such as ``"100"`` or ``"1x1"`` into a number.

    Parameters
    ----------
    text : `str`
        The setting as the camera profile keeps it. A binning such as
        ``"2x2"`` counts as 2.

    Returns
    -------
    number : `int` or `None`
        The number, or `None` when the text is not a number (an ISO such as
        ``"Auto"``).
    """
    cleaned = text.strip().casefold()
    if "x" in cleaned:
        first, _, second = cleaned.partition("x")
        if first != second:
            return None
        cleaned = first
    try:
        return round(float(cleaned))
    except ValueError:
        return None


def allowed_capture_values(
    camera_profile: CameraProfileLike,
    configured_filter_names: Iterable[str] = (),
    library_filter_names: Iterable[str] = (),
) -> AllowedCaptureValues:
    """Collect what a camera may be asked to do.

    Parameters
    ----------
    camera_profile : `CameraProfileLike`
        The camera's profile, from `resolve_camera_profile`. Its measured
        settings are the allowed ones. The generic fallback profile counts
        as having none.
    configured_filter_names : `Iterable` [`str`], optional
        The filters the equipment configuration lists.
    library_filter_names : `Iterable` [`str`], optional
        The filters seen on this camera's frames in the library. Used only
        when the equipment configuration lists none.

    Returns
    -------
    allowed : `AllowedCaptureValues`
        The allowed values, with a `check` method for a request.
    """
    measured: list[MeasuredCaptureSetting] = []
    if not camera_profile.is_generic_fallback:
        for entry in camera_profile.sensor_noise:
            gain_setting = _as_whole_number(entry.gain_setting)
            offset = _as_whole_number(entry.offset)
            binning = _as_whole_number(entry.binning)
            if gain_setting is None or offset is None or binning is None:
                continue
            candidate = MeasuredCaptureSetting(gain_setting=gain_setting, offset=offset, binning=binning)
            if candidate not in measured:
                measured.append(candidate)
    configured = sorted({name for name in configured_filter_names if name})
    from_library = sorted({name for name in library_filter_names if name})
    if configured:
        filters, filter_source = configured, "equipment"
    elif from_library:
        filters, filter_source = from_library, "library"
    else:
        filters, filter_source = [], "none"
    return AllowedCaptureValues(
        camera_name=camera_profile.camera_name,
        settings_measured=bool(measured),
        measured_settings=measured,
        filters=filters,
        filter_source=filter_source,
    )


def camera_names_match(first: str, second: str, config: Any = None) -> bool:
    """Say whether two camera names are the same camera.

    This is the one camera-name matcher of the quality code. The catalog name,
    the FITS header name and the library record name of a camera can all
    differ, so the comparison uses every spelling the camera profile lists
    (case, spaces and punctuation are ignored).

    Parameters
    ----------
    first, second : `str`
        The two camera names.
    config : `AppConfiguration`, optional
        The configuration that holds the camera profiles. Without it, or for
        a camera with no profile, the names are compared as written.

    Returns
    -------
    same : `bool`
        `True` when the names refer to one camera.
    """
    spellings: set[str] = set()
    for name in (first, second):
        spellings_of_name = {normalize_name(name)}
        try:
            profile = resolve_camera_profile(name, config)
            spellings_of_name.update(
                normalize_name(alias)
                for alias in (profile.camera_name, profile.record_name, *profile.name_aliases)
                if alias and not profile.is_generic_fallback
            )
        except ConfigurationError:
            pass
        if spellings and not (spellings & spellings_of_name):
            return False
        spellings |= spellings_of_name
    return True


def allowed_capture_values_for_camera(
    camera_name: str, config: Any, library_settings: Iterable[CaptureSetting]
) -> AllowedCaptureValues:
    """Collect what the camera of a night plan may be asked to do.

    The measured settings come from the camera profile. The equipment catalog
    lists no filters today, so the filters are the ones seen on the camera's
    frames in the library (and `allowed_capture_values` takes a catalog list
    first as soon as the catalog has one).

    Parameters
    ----------
    camera_name : `str`
        The camera named in the plan.
    config : `AppConfiguration`
        The configuration that holds the camera profiles.
    library_settings : `Iterable` [`CaptureSetting`]
        Every capture setting in the measurement store. Those of this camera
        give the filters.

    Returns
    -------
    allowed : `AllowedCaptureValues`
        The allowed values, with a `check` method for a request.
    """
    try:
        profile = resolve_camera_profile(camera_name, config)
    except ConfigurationError:
        # A configuration with no camera profiles cannot say what was
        # measured. Treat the camera as unprofiled: requests are accepted and
        # marked.
        profile = SimpleNamespace(camera_name=camera_name, is_generic_fallback=True, sensor_noise=())
    library_filters = [
        setting.filter_name
        for setting in library_settings
        if setting.filter_name and camera_names_match(setting.camera, camera_name, config)
    ]
    return allowed_capture_values(profile, (), library_filters)
