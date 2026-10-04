"""Load, save, and expose access to the application configuration."""

import configparser
import logging
import os
from pathlib import Path

import tomlkit

from .camera_names import normalize_camera_name
from .enums import FilterType
from .observatory_setups import ObservatorySetups, load_observatory_setups

_instance = None

_UNSET = object()


class _TomlSectionedConfig:
    """A configparser-compatible view over a flat-sectioned TOML document.

    Every section is stored as a single top-level TOML table keyed by its
    full historical dotted name (e.g. ``"Observatory.Camera.Nikon D5300"``)
    rather than as real nested TOML tables, so every accessor on
    `AppConfiguration` written against `configparser`'s flat-section model
    keeps working unchanged -- this class only swaps what parses/writes the
    file on disk. Plain values are stored and returned as strings, matching
    `configparser`'s own everything-is-a-string behavior, so existing
    `float()`/`int()`/`.lower()` conversions elsewhere keep working. Inline
    tables (used by camera profile fields such as `clip_ceiling_adu`) are
    the one exception: they come back as `tomlkit`'s own dict-like objects
    with real, native types, since those are read by a purpose-built loader
    (`camera_profile_store.py`), not by these generic string-based getters.
    """

    def __init__(self) -> None:
        """Start with an empty TOML document."""
        self._document: tomlkit.TOMLDocument = tomlkit.document()

    def read(self, path: str, encoding: str = "utf-8") -> list[str]:
        """Parse a TOML file into this config, `configparser.read`-style.

        Returns
        -------
        read_paths : `list` [`str`]
            `[path]` if the file was read successfully, `[]` otherwise.
        """
        try:
            text = Path(path).read_text(encoding=encoding)
        except OSError:
            return []
        self._document = tomlkit.parse(text)
        return [path]

    def write(self, fileobj) -> None:  # ruff: ignore[missing-type-function-argument]
        """Serialize this config to `fileobj`, keeping comments/formatting."""
        fileobj.write(tomlkit.dumps(self._document))

    def read_string(self, text: str) -> None:
        """Parse TOML text into this config, `configparser`-style."""
        self._document = tomlkit.parse(text)

    def sections(self) -> list[str]:
        """Return every section's full flat name.

        Returns
        -------
        section_names : `list` [`str`]
            Every top-level section name, in document order.
        """
        return list(self._document.keys())

    def has_section(self, section: str) -> bool:
        """Return whether `section` exists.

        Returns
        -------
        exists : `bool`
            `True` if `section` is present.
        """
        return section in self._document

    def add_section(self, section: str) -> None:
        """Add an empty section named `section`."""
        self._document[section] = tomlkit.table()

    def set(self, section: str, key: str, value: object) -> None:
        """Set `key` within `section` to `str(value)`."""
        self._document[section][key] = str(value)

    def get(self, section: str, key: str, fallback: object = _UNSET) -> object:
        """Return `section`'s `key`, `configparser.get`-style.

        Returns
        -------
        value : `Any`
            The stored value, stringified, or `fallback` if `section`/`key`
            does not exist and `fallback` was given.

        Raises
        ------
        KeyError
            If `section`/`key` does not exist and no `fallback` was given.
        """
        try:
            return str(self._document[section][key])
        except KeyError:
            if fallback is _UNSET:
                raise
            return fallback

    def getboolean(self, section: str, key: str, fallback: object = _UNSET) -> object:
        """Return `section`'s `key` as a bool, `configparser.getboolean`-style.

        Returns
        -------
        value : `bool` or `Any`
            The stored value interpreted as a bool, or `fallback` if
            `section`/`key` does not exist and `fallback` was given.

        Raises
        ------
        KeyError
            If `section`/`key` does not exist and no `fallback` was given.
        """
        try:
            raw = self._document[section][key]
        except KeyError:
            if fallback is _UNSET:
                raise
            return fallback
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("true", "1", "yes", "on")

    def __contains__(self, section: str) -> bool:
        """Return whether `section` exists.

        Returns
        -------
        exists : `bool`
            `True` if `section` is present.
        """
        return section in self._document

    def __getitem__(self, section: str) -> object:
        """Return `section`'s table.

        Returns
        -------
        table : `Any`
            The section's underlying TOML table.
        """
        return self._document[section]


def get_configuration() -> AppConfiguration:
    """Return a singleton instance of the AppConfiguration.

    Ensures the config is only loaded from disk once.

    Returns
    -------
    configuration : `AppConfiguration`
        The process-wide singleton configuration instance.
    """
    global _instance
    if _instance is None:
        _instance = AppConfiguration()
    return _instance


class AppConfiguration:
    """Load and save the configuration of the app.

    Uses pathlib for robust cross-platform path management.
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        # Get the directory of the current script
        self.base_dir = Path(__file__).parent.absolute()
        self.app_config = _TomlSectionedConfig()
        self.config_file_path: Path | None = None
        self.load_configuration()

    def _find_config_file(self) -> Path:
        """Locates the high-level interface.

        config file in expected locations.

        Returns
        -------
        config_path : `Path`
            The resolved configuration file path, existing or not.
        """
        import os

        env_path = os.getenv("ASTROMETRICS_CONFIG_PATH") or os.getenv("ASTROMETRICS_CONFIG")
        if env_path:
            p = Path(env_path)
            if p.is_file():
                return p
            # If specified but doesn't exist yet, it is still defaulted to so
            # it gets created there on save
            return p

        candidates = [
            self.base_dir.parent
            / "astrometrics.config.toml",  # astrometricslib/astrometrics.config.toml (primary user location)
            self.base_dir.parent.parent
            / "backend"
            / "astrometrics.config.toml",  # Backend root folder (Repo/backend/astrometrics.config.toml)
        ]

        for p in candidates:
            if p.is_file():
                return p

        # Default to primary astrometrics folder if not found
        return candidates[0]

    def save_configuration(self) -> None:
        """Save the current config to the resolved astrometrics config file."""
        import os

        path = self.config_file_path or self._find_config_file()
        if os.getenv("ASTROMETRICS_TESTING") == "1" and not (
            os.getenv("ASTROMETRICS_CONFIG_PATH") or os.getenv("ASTROMETRICS_CONFIG")
        ):
            # Guard against writing to repository production config
            # during testing.
            return
        with open(path, "w", encoding="utf-8") as configfile:
            self.app_config.write(configfile)

    def _populate_defaults(self) -> None:
        """Populate the config with sensible defaults if it's empty."""
        defaults = {
            "Image Library": {
                "path": "./library",
            },
            "Observatory.Telescope": {
                "hostname": "localhost",
                "indi_port": "7624",
                "focal_length_mm": "0.0",
                "focal_ratio": "0.0",
                "remote_pictures_path": "/home/stellarmate/Pictures",
            },
            "Observatory.Camera": {"default_primary_camera": "Unknown", "models": "Unknown"},
            "Observatory.Constraints": {"min_altitude": "0.0", "max_altitude": "90.0"},
            "Processing.Siril": {
                # The -cli entry point, matching the config template.
                # Plain "siril" is the GUI build: it needs a display
                # connection and so fails in headless pipe mode, which is how
                # every stack runs. This default is what a configuration
                # written before [Processing.Siril] existed falls back to, so
                # it has to be the working value, not the historical one.
                "siril_executable": "siril-cli",
                "rejection_sigma_mode": "adaptive",
                "rejection_sigma_low": "3.0",
                "rejection_sigma_high": "3.0",
                "filter_wfwhm_percentile": "",
                "filter_round_percentile": "",
                # Blank, matching the config template: -weight= needs
                # a newer Siril than the default apt install provides, and a
                # default that breaks the default install is not a default.
                "stack_weight": "",
                "generate_rejmap": "true",
                "background_homogeneity_check_enabled": "true",
                "quarantine_bad_frames_enabled": "true",
                "preview_star_tone_enabled": "true",
                "keep_previous_stack_enabled": "true",
                "trim_noisy_stack_edges_enabled": "true",
                "auto_open_gui": "false",
            },
            # Blank: no gradient removal. Stack previews are then stretched
            # without it. See get_graxpert_executable.
            "Processing.GraXpert": {"graxpert_executable": ""},
            # Blank: no denoising. See get_cosmic_clarity_denoise_executable.
            "Processing.CosmicClarity": {"denoise_executable": "", "denoise_strength": "0.9"},
            # 500; see get_maximum_identified_stars for why this isn't 0
            # (unlimited) despite that having been this setting's first
            # default.
            "Processing.Astrometry": {"maximum_identified_stars": "500"},
        }
        for section, values in defaults.items():
            if section not in self.app_config:
                self.app_config.add_section(section)
            for key, value in values.items():
                if key not in self.app_config[section]:
                    self.app_config.set(section, key, value)

    def load_configuration(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Load the configuration from the best available candidate.

        Creates one with defaults if missing.
        """
        path = self._find_config_file()
        self.config_file_path = path

        import logging

        logger = logging.getLogger(__name__)

        if path.is_file():
            if self.app_config.read(str(path), encoding="utf-8"):
                logger.info(f"Loaded configuration from: {path}")
                self._populate_defaults()
            else:
                logger.warning(f"Failed to read configuration from: {path}. Using defaults.")
                self._populate_defaults()
                self.save_configuration()
        else:
            logger.warning(f"Configuration file not found at {path}. Creating with defaults.")
            self._populate_defaults()
            self.save_configuration()

    def get_siril_executable(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Retrieve the Siril executable path from the configuration.

        Returns
        -------
        executable_path : `str` or `None`
            Path or command name for the Siril executable.
        """
        # Special case: check Processing.Siril first, then Image Library
        val = self.app_config.get("Processing.Siril", "siril_executable", fallback=None)
        if val is not None:
            return val
        return self.app_config.get("Image Library", "siril_executable", fallback=None)

    def get_graxpert_executable(self) -> str | None:
        """Retrieve the command that starts GraXpert, if one is set.

        GraXpert removes the sky gradient from a stack before its preview
        picture is stretched. A blank setting turns that step off.

        Returns
        -------
        executable : `str` or `None`
            The command (a path, or a command with arguments), or `None` if
            the setting is blank or missing.
        """
        return self.get_value("Processing.GraXpert", "graxpert_executable", fallback="") or None

    def get_cosmic_clarity_denoise_executable(self) -> str | None:
        """Retrieve the path of Cosmic Clarity's denoise program, if set.

        Cosmic Clarity (SetiAstro) removes noise with an AI model. It runs on
        the stretched copy of a stack, after the sky gradient is removed and
        the stretch is applied, and before that copy is saved as the preview
        picture. A blank setting turns the step off.

        Returns
        -------
        executable : `str` or `None`
            The path of ``SetiAstroCosmicClarity_denoise``, or `None` if the
            setting is blank or missing. The program works in the ``input``
            and ``output`` folders next to it.
        """
        return self.get_value("Processing.CosmicClarity", "denoise_executable", fallback="") or None

    def get_cosmic_clarity_denoise_strength(self) -> float:
        """Return how strongly Cosmic Clarity removes noise, from 0 to 1.

        Returns
        -------
        strength : `float`
            The configured strength, kept between 0 and 1. A missing or
            unreadable setting gives 0.9. On the M 101, M 57, NGC 4438 and
            M 81 stacks, 0.9 cut the sky grain by 14-39% compared with 0.75
            and left star peaks unchanged. On the M 13 stack, 0.5 left
            visible grain. The denoise runs after the stretch.
        """
        raw = self.get_value("Processing.CosmicClarity", "denoise_strength", fallback="0.9")
        try:
            return min(1.0, max(0.0, float(raw)))
        except TypeError, ValueError:
            return 0.9

    def get_stack_rejection_sigma_mode(self) -> str:
        """Return the configured stack-time pixel rejection sigma mode.

        Either "adaptive" (Chauvenet's criterion, scaled to frame count)
        or "fixed". Adaptive is the default: rejection_threshold_analysis.py
        sweeps against M 81, M 13, and NGC 2403 found stacked-image FWHM
        indistinguishable between sigma=2.5 and sigma=3.0, so the lower,
        frame-count-derived Chauvenet sigma (see
        utilities/rejection_thresholds.py) costs no measurable
        sharpness while rejecting a more statistically-justified fraction of
        pixels.
        "fixed" falls back to get_stack_rejection_sigma()'s configured
        constant for callers that want the old fixed-sigma behavior.

        Returns
        -------
        mode : `str`
            Either ``"adaptive"`` or ``"fixed"``.
        """
        val = self.get_value("Processing.Siril", "rejection_sigma_mode", fallback="adaptive")
        return str(val).lower()

    def get_stack_rejection_sigma(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the configured (sigma_low, sigma_high) rejection pair.

        This fixed pair is only used when get_stack_rejection_sigma_mode()
        is "fixed", or as an explicit rejection_override passed by a
        caller -- adaptive mode computes its own sigma from frame count
        instead of reading this value.

        Returns
        -------
        sigma_pair : `tuple` [`float`, `float`]
            The configured ``(sigma_low, sigma_high)`` pair.
        """
        low = self.get_value("Processing.Siril", "rejection_sigma_low", fallback="3.0")
        high = self.get_value("Processing.Siril", "rejection_sigma_high", fallback="3.0")
        return (float(low), float(high))

    def get_stack_filter_wfwhm_percentile(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the configured -filter-wfwhm value, or None if disabled.

        Returns
        -------
        percentile : `str` or `None`
            The configured percentile, or `None` if disabled.
        """
        return self.get_value("Processing.Siril", "filter_wfwhm_percentile", fallback="") or None

    def get_stack_filter_round_percentile(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the configured -filter-round value, or None if disabled.

        Returns
        -------
        percentile : `str` or `None`
            The configured percentile, or `None` if disabled.
        """
        return self.get_value("Processing.Siril", "filter_round_percentile", fallback="") or None

    def get_stack_weight(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the configured Siril -weight= mode, or None if disabled.

        Returns
        -------
        weight_mode : `str` or `None`
            The configured weight mode, or `None` if disabled.
        """
        return self.get_value("Processing.Siril", "stack_weight", fallback="") or None

    def get_stack_generate_rejmap(self) -> bool:
        """Return whether Siril should generate a rejection map (-rejmap).

        Returns
        -------
        generate_rejmap : `bool`
            `True` if a rejection map should be generated.
        """
        val = self.get_value("Processing.Siril", "generate_rejmap", fallback="true")
        return str(val).lower() == "true"

    def get_background_homogeneity_check_enabled(self) -> bool:
        """Return whether the per-frame background-homogeneity check runs.

        Reads and computes sigma-clipped background stats for every input
        frame (~1.4s/frame measured against real ZWO ASI 533MM Pro FITS
        data), so this adds real, bounded overhead per stack (e.g. ~100s
        for a 70-frame session) on top of the stacking job itself.
        Defaults on since it's what caught the real NGC 2403 cloud event
        that rejection-fraction and gain/calibration checks all missed,
        but exposed as a toggle for cases where that per-stack cost isn't
        acceptable.

        Returns
        -------
        enabled : `bool`
            `True` if the background-homogeneity check should run.
        """
        val = self.get_value("Processing.Siril", "background_homogeneity_check_enabled", fallback="true")
        return str(val).lower() == "true"

    def get_quarantine_bad_frames_enabled(self) -> bool:
        """Return whether stacking moves cloudy or trailed frames aside.

        When on, each stack measures its light frames and moves the ones with
        clouds or trailed stars into an ``_excluded`` folder beside them (see
        `pipelines/stacking/pre_processing/frame_quarantine.py`). The frames
        are never deleted and `restore_excluded_frames.py` moves them back.
        Measuring takes about a second per frame.

        Returns
        -------
        enabled : `bool`
            `True` if the quarantine step should run.
        """
        val = self.get_value("Processing.Siril", "quarantine_bad_frames_enabled", fallback="true")
        return str(val).lower() == "true"

    def get_trim_noisy_stack_edges_enabled(self) -> bool:
        """Return whether a finished imaging stack has its noisy edges trimmed.

        When on, each imaging stack (and its rejection map) is cut back to
        where the edge noise falls within 15% of the interior's (see
        `pipelines/stacking/post_processing/stack_crop.py`). A stack with
        clean edges is not changed.

        Returns
        -------
        enabled : `bool`
            `True` if the trim should run.
        """
        val = self.get_value("Processing.Siril", "trim_noisy_stack_edges_enabled", fallback="true")
        return str(val).lower() == "true"

    def get_keep_previous_stack_enabled(self) -> bool:
        """Return whether a restack keeps the stack it replaces.

        When on, a restack first moves the current stack and its pictures into
        a ``_previous`` folder beside it (see
        `pipelines/stacking/post_processing/previous_stack.py`). Only one
        previous version is kept; the next restack replaces it. Nothing
        deletes it until the user calls `discard_previous_stack`.

        Returns
        -------
        enabled : `bool`
            `True` if the previous stack should be kept.
        """
        val = self.get_value("Processing.Siril", "keep_previous_stack_enabled", fallback="true")
        return str(val).lower() == "true"

    def get_preview_star_tone_enabled(self) -> bool:
        """Return whether the stack preview tones down its stars.

        When on, the preview dims stars in proportion to their brightness and
        compresses the brightest values below pure white (see
        `pipelines/stacking/post_processing/star_tone.py`). The stack itself
        is never changed.

        Returns
        -------
        enabled : `bool`
            `True` if the star toning step should run.
        """
        val = self.get_value("Processing.Siril", "preview_star_tone_enabled", fallback="true")
        return str(val).lower() == "true"

    def get_maximum_identified_stars(self) -> int | None:
        """Return the maximum number of stars to identify in an image.

        Defaults to 500. This limits how many detected stars are matched
        against a database. Identifying every single star in a dense area
        takes a lot of time and uses too much memory (RAM), which can crash
        the computer.

        Limiting it to the 500 brightest stars gives us plenty of data for
        tracking and analysis without overloading the system. A user who
        has enough memory and wants to find every single star can change
        this setting to 0 (unlimited).

        Returns
        -------
        limit : int or None
            The maximum number of stars to identify. 0 means unlimited.
        """
        val = self.get_value("Processing.Astrometry", "maximum_identified_stars", fallback="500")
        try:
            maximum = int(str(val).strip())
        except TypeError, ValueError:
            return None
        return maximum if maximum > 0 else None

    def get_auto_open_siril_gui(self) -> bool:
        """Return whether Siril GUI should be opened when stacking finishes.

        Returns
        -------
        auto_open : `bool`
            `True` if Siril GUI should automatically open post-stacking.
        """
        val = self.get_value("Processing.Siril", "auto_open_gui", fallback="false")
        return str(val).lower() == "true"

    def get_telescope_hostname(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Retrieve the telescope hostname from the configuration.

        Returns
        -------
        hostname : `str`
            The configured telescope hostname, defaulting to
            ``"localhost"``.
        """
        return self.app_config.get("Observatory.Telescope", "hostname", fallback="localhost")

    def get_indi_host(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the INDI server host, i.e. the telescope hostname.

        Returns
        -------
        hostname : `str`
            The configured telescope hostname.
        """
        return self.get_telescope_hostname()

    def get_indi_port(self) -> int:
        """Return the INDI server port, falling back to 7624.

        Returns
        -------
        port : `int`
            The configured INDI server port.
        """
        val = self.app_config.get("Observatory.Telescope", "indi_port", fallback="7624")
        return int(val)

    def get_camera_config(self, camera_name=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Return the configuration section for a named camera.

        Parameters
        ----------
        camera_name : `str`, optional
            The camera to look up. If `None` (default), the configured
            default primary camera is used instead.

        Returns
        -------
        config : `dict`
            The matching section's key/value pairs, checked in order of
            ``Observatory.Camera.<camera_name>`` (exact, then matched
            ignoring spaces/case), then ``Observatory.Camera``. Returns
            an empty dict if no camera name is resolved or no section
            matches.
        """
        if not camera_name:
            # Fallback to default primary camera
            camera_name = self.app_config.get("Observatory.Camera", "default_primary_camera", fallback=None)
            if not camera_name:
                return {}

        exact_section = f"Observatory.Camera.{camera_name}"
        if exact_section in self.app_config:
            return dict(self.app_config[exact_section])

        # Camera names in settings and image files often differ slightly
        # in spacing/capitalization (e.g. "ZWO ASI533MM Pro" in a config
        # file typed by hand vs. "ZWO ASI 533MM Pro" as the camera's own
        # FITS header spells it). Matching loosely here, with the same
        # `normalize_camera_name` the rest of the library uses, keeps a
        # real per-camera section (dispersion geometry, grating spacing,
        # etc.) from being
        # silently skipped over a formatting difference -- which
        # otherwise falls through to the generic `[Observatory.Camera]`
        # section's bare model list and produces nonsensical spectroscopy
        # defaults (e.g. a zero-length extraction) with no error at all.
        camera_prefix = "Observatory.Camera."
        for section in self.app_config.sections():
            if not section.startswith(camera_prefix):
                continue
            section_camera_name = section[len(camera_prefix) :]
            if normalize_camera_name(section_camera_name) == normalize_camera_name(camera_name):
                return dict(self.app_config[section])

        if "Observatory.Camera" in self.app_config:
            return dict(self.app_config["Observatory.Camera"])

        return {}

    def get_available_cameras(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return a list of available camera names from configuration.

        Returns
        -------
        camera_names : `list` [`str`]
            Configured camera model names.
        """
        models_str = self.app_config.get("Observatory.Camera", "models", fallback=None)
        return [m.strip() for m in models_str.split(",")] if models_str else []

    def get_available_filters(self) -> list[FilterType]:
        """Return the optical filters installed at this observatory.

        This is the static inventory of filters the filter wheel is
        loaded with -- not its live position, which is queried directly
        from the INDI device (see wayfindinglib's ``FilterWheelController``).

        Returns
        -------
        filters : `list` [`FilterType`]
            Configured filters, in the order listed under
            ``[Observatory.Filters] available``.
        """
        filters_str = self.app_config.get("Observatory.Filters", "available", fallback="")
        if not filters_str:
            return []
        return [FilterType[name.strip().upper()] for name in filters_str.split(",") if name.strip()]

    def get_all_config(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the entire configuration as a dictionary of sections.

        Returns
        -------
        config_dict : `dict`
            Mapping of section name to its key/value pairs.
        """
        config_dict = {}
        for section in self.app_config.sections():
            config_dict[section] = dict(self.app_config[section])
        return config_dict

    def get_value(self, section, key, fallback=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Safe wrapper for getting a config value.

        Returns
        -------
        value : `Any`
            The resolved config value, or `fallback` if not found.
        """
        try:
            return self.app_config.get(section, key, fallback=fallback)
        except configparser.NoSectionError, configparser.NoOptionError, KeyError:
            return fallback

    def get_focal_length_mm(self) -> float:
        """Return the telescope focal length in mm from the configuration.

        Returns
        -------
        focal_length_mm : `float`
            The configured focal length, in millimeters.
        """
        val = self.app_config.get("Observatory.Telescope", "focal_length_mm", fallback="0.0")
        return float(val)

    def get_primary_focal_length_mm(self) -> float | None:
        """Return the focal length of the observer's primary optic, in mm.

        A library may hold frames from several optics -- this one holds
        1,596 at 300mm and 1,055 at 405mm -- and each needs its own
        stack, since blending scales that differ by 1.35x produces an
        image with no single pixel scale. This value decides which of
        those stacks a target's `stacked_image` points at by default.

        Reads ``[Observatory.Telescope] focal_length_mm``, i.e. the optic
        already described as the observatory's own.

        Returns
        -------
        focal_length_mm : `float` or `None`
            The configured primary focal length, or `None` when it is
            unset or zero, in which case callers fall back to whichever
            configuration has the most frames.
        """
        focal_length = self.get_focal_length_mm()
        return focal_length if focal_length and focal_length > 0 else None

    def get_primary_camera_name(self) -> str | None:
        """Return the observer's primary camera name, if one is configured.

        Used with `get_primary_focal_length_mm` to decide which of a
        target's stacks its `stacked_image` points at. Camera alone is
        not enough -- one camera used through two optics produces two
        stacks that must not be conflated -- and focal length alone is
        not either, since two cameras can share a focal length.

        Returns
        -------
        camera_name : `str` or `None`
            The configured ``default_primary_camera``, or `None` when
            unset.
        """
        camera_name = self.app_config.get("Observatory.Camera", "default_primary_camera", fallback="")
        camera_name = (camera_name or "").strip()
        return camera_name or None

    def get_observatory_setups(self) -> ObservatorySetups:
        """Return the optics and the camera-and-optic pairings in the config.

        The pairings say which cameras are really used with which optics.
        They are read from the ``[Observatory.Optics]``,
        ``[Observatory.Optic.<name>]``, ``[Observatory.Setups]`` and
        ``[Observatory.Setup.<name>]`` sections (see
        `astrometricslib.utilities.observatory_setups`).

        Returns
        -------
        observatory_setups : `ObservatorySetups`
            The optics and setups. Both are empty when the config has none.
        """
        return load_observatory_setups(self)

    def get_camera_default_iso(self, camera_name: str | None) -> str | None:
        """Return the ISO or gain to assume for a camera whose header has none.

        Read from the ``default_iso`` key of the camera's section. It is used
        only when an image's header records neither ``ISOSPEED`` nor ``GAIN``.

        Parameters
        ----------
        camera_name : `str` or `None`
            The camera, written in any spelling that matches its section.

        Returns
        -------
        default_iso : `str` or `None`
            The configured value, or `None` when the camera's section does
            not give one.
        """
        if not camera_name:
            return None
        value = self.get_camera_config(camera_name).get("default_iso")
        value = (value or "").strip()
        return value or None

    def get_focal_ratio(self) -> float:
        """Return the telescope focal ratio from the configuration.

        Returns
        -------
        focal_ratio : `float`
            The configured focal ratio.
        """
        val = self.app_config.get("Observatory.Telescope", "focal_ratio", fallback="0.0")
        return float(val)

    def get(self, *args, **kwargs):  # ruff: ignore[missing-type-args, missing-type-kwargs, missing-return-type-undocumented-public-function]
        """Proxy to internal ConfigParser get method.

        Returns
        -------
        value : `Any`
            The value returned by the underlying `ConfigParser.get`
            call.
        """
        return self.app_config.get(*args, **kwargs)

    def update_config(self, new_config_dict):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Update the configuration with the provided dictionary and saves it.

        Args: new_config_dict: Dictionary { "SectionName": { "Key": "Value" } }
        """
        for section, params in new_config_dict.items():
            if not self.app_config.has_section(section):
                self.app_config.add_section(section)
            for key, value in params.items():
                self.app_config.set(section, key, str(value))
        self.save_configuration()

    def get_project_root(self) -> Path:
        """Return the absolute path to the project root.

        Returns
        -------
        project_root : `Path`
            Absolute path to the project root directory.
        """
        # self.base_dir = <repo root>/astrometricslib/utilities/
        return self.base_dir.parent.parent.absolute()

    def get_library_path(self) -> Path:
        """Return the absolute path to the image library's `library` path.

        Returns
        -------
        library_path : `Path`
            Absolute path to the resolved library directory.
        """
        try:
            path_str = self.app_config.get("Image Library", "path")
            path = Path(path_str)
            if not path.is_absolute():
                # If path starts with ./, resolve relative to project root.
                # If `library` was moved to astrometricslib/library,
                # check there.
                check_path = self.get_project_root() / "astrometricslib" / path
                if check_path.exists():
                    return check_path.absolute()
                return (self.get_project_root() / path).absolute()
            return path.absolute()
        except configparser.NoSectionError, configparser.NoOptionError, KeyError:
            check_path = self.get_project_root() / "astrometricslib" / "library"
            if check_path.exists():
                return check_path.absolute()
            return (self.get_project_root() / "library").absolute()

    def get_frames_path(self) -> Path:
        """Return the absolute path to the frames directory.

        Reads the ``"frames_path"`` entry from the ``[Image Library]`` section.
        If omitted or empty, defaults to a `"frames"` subfolder of the library
        path. Handles mount path resolution between ``/run/media`` and
        ``/media`` if one is configured but the other is currently mounted.

        Returns
        -------
        frames_path : `Path`
            Absolute path to the resolved frames directory.
        """
        try:
            path_str = self.app_config.get("Image Library", "frames_path")
            if path_str:
                path = Path(path_str)
                if not path.is_absolute():
                    check_path = self.get_project_root() / "astrometricslib" / path
                    if check_path.exists():
                        path = check_path.absolute()
                    else:
                        path = (self.get_project_root() / path).absolute()
                else:
                    path = path.absolute()
                if not path.exists():
                    p_str = str(path)
                    if p_str.startswith("/run/media/"):
                        alt = Path(p_str.replace("/run/media/", "/media/", 1))
                        if alt.exists():
                            path = alt
                    elif p_str.startswith("/media/"):
                        alt = Path(p_str.replace("/media/", "/run/media/", 1))
                        if alt.exists():
                            path = alt
                return path
        except configparser.NoSectionError, configparser.NoOptionError, KeyError:
            pass
        return self.get_library_path() / "frames"

    def get_frames_mount_point(self) -> Path | None:
        """Return the folder where the raw frames' drive must be mounted.

        Reads the optional ``"frames_mount_point"`` entry from the
        ``[Image Library]`` section. Set it when the frames live on a drive
        that is not always attached, such as a USB disk or a network share.
        Downloads and file sorting then refuse to write below it unless
        something is mounted there (see
        `astrometricslib.utilities.storage_mount`). It is usually the frames
        path or a folder above it.

        Returns
        -------
        mount_point : `Path` or `None`
            The absolute mount point, or `None` if the entry is missing or
            empty, which turns the check off.
        """
        try:
            path_str = self.app_config.get("Image Library", "frames_mount_point")
        except configparser.NoSectionError, configparser.NoOptionError, KeyError:
            return None
        if not path_str:
            return None
        return Path(path_str).absolute()

    def get_stacks_path(self) -> Path:
        """Return the absolute path where stacks and other derived files go.

        Reads the optional ``"stacks_path"`` entry from the ``[Image Library]``
        section. Stacks, group stacks, rejection maps, previews and processed
        pictures are written under ``<stacks_path>/lights/<target>/``. This
        lets the large raw frames stay on one disk while the files the
        pipeline makes go to another. If the entry is omitted or empty, the
        files go beside the raw frames, in the frames path.

        Returns
        -------
        stacks_path : `Path`
            Absolute path of the folder that holds the ``lights`` folder for
            derived files. The folder is not created here.
        """
        try:
            path_str = self.app_config.get("Image Library", "stacks_path")
        except configparser.NoSectionError, configparser.NoOptionError, KeyError:
            path_str = None
        if not path_str:
            return self.get_frames_path()
        path = Path(path_str)
        if not path.is_absolute():
            path = self.get_project_root() / path
        return path.absolute()

    def get_library_file_path(self, filename: str) -> Path:
        """Return the absolute path to a file within `library`.

        Returns
        -------
        file_path : `Path`
            Absolute path to `filename` within the library directory.
        """
        return self.get_library_path() / filename

    def get_logs_db_path(self) -> str:
        """Return the absolute path to the logs database (astrometrics_log.

        db).

        Returns
        -------
        logs_db_path : `str`
            Absolute path to the logs database file.
        """
        return str(self.get_library_file_path("astrometrics_log.db"))

    def get_logs_path(self) -> Path:
        """Return the absolute path to the logs directory.

        Returns
        -------
        logs_path : `Path`
            Absolute path to the logs directory, created if it did
            not already exist.
        """
        path = self.get_project_root() / "logs"
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_frames_file_path(self, filename: str) -> Path:
        """Return the absolute path to a file within the frames directory.

        Returns
        -------
        file_path : `Path`
            Absolute path to `filename` within the frames directory.
        """
        return self.get_frames_path() / filename

    def get_remote_pictures_path(self) -> str:
        """Return the remote path for pictures on the telescope controller.

        Returns
        -------
        remote_pictures_path : `str`
            Configured remote pictures path on the telescope host.
        """
        return self.app_config.get(
            "Observatory.Telescope", "remote_pictures_path", fallback="/home/stellarmate/Pictures"
        )

    def get_remote_transfer_driver_name(self) -> str:
        """Return the configured remote file-transfer protocol name.

        Selects which `RemoteTransferDriver` implementation
        `ObservatoryControl.remote_transfer_driver` builds
        (`wayfindinglib/drivers/protocols/remote_transfer_driver.py`) --
        a separate, pluggable choice from the hardware-control protocol,
        since pulling files off a telescope host is not part of INDI or
        ASCOM.

        Returns
        -------
        driver_name : `str`
            The configured remote-transfer driver name, defaulting to
            ``"stellarmate"``.
        """
        return self.app_config.get("Observatory.RemoteTransfer", "driver", fallback="stellarmate")

    def get_min_altitude(self) -> float:
        """Return the minimum allowed altitude for telescope slews.

        Returns
        -------
        min_altitude : `float`
            Minimum allowed altitude, in degrees.
        """
        try:
            return float(self.app_config.get("Observatory.Constraints", "min_altitude", fallback="0.0"))
        except ValueError, configparser.Error:
            return 0.0

    def get_max_altitude(self) -> float:
        """Return the maximum allowed altitude for telescope slews.

        Returns
        -------
        max_altitude : `float`
            Maximum allowed altitude, in degrees.
        """
        try:
            return float(self.app_config.get("Observatory.Constraints", "max_altitude", fallback="90.0"))
        except ValueError, configparser.Error:
            return 90.0

    def get_target_workers(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the configured target worker count, or "auto" for sizing.

        Returns
        -------
        target_workers : `str`
            Configured worker count, or ``"auto"``.
        """
        return self.get_value("Processing.Parallelism", "target_workers", fallback="auto")

    def get_max_concurrent_jobs(self) -> int:
        """Return the max concurrent heavy jobs allowed, system-wide.

        Covers both Siril stacking and photometry/spectroscopy analysis
        sessions, sharing one slot pool: they answer the same question
        ("how many heavy background jobs at once") for a single-machine
        deployment, so one setting throttles both rather than requiring
        two independently-tuned resource pools.

        Returns
        -------
        max_concurrent_jobs : `int`
            Maximum number of concurrent heavy jobs.
        """
        # An environment override is honoured first so a value can reach
        # worker processes. Batch work runs across a ProcessPoolExecutor,
        # and those workers re-import and re-read configuration, so
        # patching this accessor in the parent reaches none of them --
        # which silently made a concurrency benchmark measure the
        # configured value at every setting: two Siril processes were
        # running during its "1 slot" measurement. The environment is
        # inherited by workers, so it does cross.
        environment_override = os.environ.get("ASTROMETRICS_MAX_CONCURRENT_JOBS")
        if environment_override:
            try:
                return max(1, int(environment_override))
            except ValueError:
                logging.getLogger(__name__).warning(
                    "Ignoring non-numeric ASTROMETRICS_MAX_CONCURRENT_JOBS=%r", environment_override
                )
        return int(self.get_value("Processing.Parallelism", "max_concurrent_jobs", fallback="2"))

    def get_photometry_workers(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the photometry worker count per target, or "auto".

        Returns
        -------
        photometry_workers : `str`
            Configured worker count, or ``"auto"``.
        """
        return self.get_value("Processing.Parallelism", "photometry_workers", fallback="auto")

    def get_worker_niceness(self) -> int:
        """Return the OS niceness value applied to batch worker processes.

        Returns
        -------
        worker_niceness : `int`
            OS niceness value for batch worker processes.
        """
        return int(self.get_value("Processing.Parallelism", "worker_niceness", fallback="10"))

    def get_schema(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return a validated AppConfigSchema for the current configuration.

        Returns
        -------
        schema : `AppConfigSchema`
            Validated configuration schema built from current values.
        """
        from .config_schema import (
            AppConfigSchema,
            CameraConfig,
            ParallelismConfig,
            ProcessingConfig,
            TelescopeConfig,
        )

        # Build telescope config
        telescope = TelescopeConfig(
            hostname=self.get_telescope_hostname(),
            focal_length_mm=self.get_focal_length_mm(),
            focal_ratio=self.get_focal_ratio(),
            remote_pictures_path=self.get_remote_pictures_path(),
        )

        # Build processing config
        rejection_sigma_low, rejection_sigma_high = self.get_stack_rejection_sigma()
        processing = ProcessingConfig(
            siril_executable=self.get_siril_executable(),
            path=str(self.get_library_path()),
            frames_path=str(self.get_frames_path()),
            rejection_sigma_mode=self.get_stack_rejection_sigma_mode(),
            rejection_sigma_low=rejection_sigma_low,
            rejection_sigma_high=rejection_sigma_high,
            filter_wfwhm_percentile=self.get_stack_filter_wfwhm_percentile(),
            filter_round_percentile=self.get_stack_filter_round_percentile(),
            stack_weight=self.get_stack_weight(),
            generate_rejmap=self.get_stack_generate_rejmap(),
            background_homogeneity_check_enabled=self.get_background_homogeneity_check_enabled(),
            quarantine_bad_frames_enabled=self.get_quarantine_bad_frames_enabled(),
        )

        # Build camera configs
        cameras = []
        for cam_name in self.get_available_cameras():
            cameras.append(
                CameraConfig(
                    name=cam_name,
                    models=self.get_available_cameras(),  # Simplified for now
                    default_primary_camera=self.app_config.get(
                        "Observatory.Camera", "default_primary_camera", fallback=None
                    ),
                )
            )

        # Build parallelism config
        parallelism = ParallelismConfig(
            target_workers=str(self.get_target_workers()),
            max_concurrent_jobs=self.get_max_concurrent_jobs(),
            photometry_workers=str(self.get_photometry_workers()),
            worker_niceness=self.get_worker_niceness(),
        )

        return AppConfigSchema(
            telescope=telescope, processing=processing, cameras=cameras, parallelism=parallelism
        )
