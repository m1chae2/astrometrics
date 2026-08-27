"""Pydantic model for the dark/bias/flat calibration frame library."""

import json
import logging
import os
import threading
from typing import Any

from astropy.io import fits
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

logger = logging.getLogger(__name__)


# Standard-system transformation coefficients derived for ZWO
# ASI533MM Pro with Baader UBVR Bessel filter set (Antonov 2026,
# JAAVSO Vol. 54).
BAADER_BESSEL_ASI533_TRANSFORM_COEFFICIENTS = {
    "T_bv": {"value": 1.095, "error": 0.009},
    "T_b_bv": {"value": 0.080, "error": 0.012},
    "T_br": {"value": 1.144, "error": 0.013},
    "T_b_br": {"value": 0.050, "error": 0.006},
    "T_v_bv": {"value": -0.008, "error": 0.015},
    "T_vr": {"value": 1.255, "error": 0.033},
    "T_v_vr": {"value": -0.012, "error": 0.025},
    "T_r_vr": {"value": -0.221, "error": 0.030},
}


class CalibrationLibrary(BaseModel):
    """Store dark, flat, and bias calibration frames."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    dark_frames: dict[str, Any] = Field(default_factory=dict)
    flat_frames: dict[str, Any] = Field(default_factory=dict)
    bias_frames: dict[str, Any] = Field(default_factory=dict)

    # Internal config state, excluded from serialization
    app_config: Any = Field(default=None, exclude=True)
    _lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)

    def __init__(self, app_config=None, **data):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-special-method]
        """Initialize calibration library with optional shared config."""
        super().__init__(**data)
        if app_config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            self.app_config = get_configuration()
        else:
            self.app_config = app_config
        # Data is loaded explicitly by the container now, but we keep
        # safety check
        if self.app_config:
            self.load_library()

    def load_library(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Load the calibration library from the JSON file."""
        if not self.app_config:
            return

        try:
            with self._lock:
                file_path = self.app_config.get_library_file_path("calibration_frames.json")
                if os.path.exists(file_path):
                    with open(file_path, encoding="utf8") as file_obj:
                        calibration_info = json.load(file_obj)
                        self.deserialize(calibration_info)
        except Exception as e:
            logger.error(f"Could not load calibration_frames.json: {e}")

    def save_library(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Save calibration library."""
        if not self.app_config:
            return

        try:
            with self._lock:
                file_path = self.app_config.get_library_file_path("calibration_frames.json")
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding="utf8") as file_obj:
                    json.dump(self.serialize(), file_obj, indent=4)
        except Exception as e:
            logger.error(f"Could not save calibration_frames.json: {e}")

    def serialize(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Serialize this calibration library to a plain dict.

        Returns
        -------
        data : `dict`
            Plain dict representation of this calibration library.
        """
        return self.model_dump()

    def deserialize(self, object_info):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Populate this calibration library from a serialized dict.

        Parameters
        ----------
        object_info : `dict`
            A dict as produced by `serialize`, mapping field names
            (``dark_frames``, ``flat_frames``, ``bias_frames``) to
            their values. Non-dict input and empty/falsy values are
            silently ignored.
        """
        if not isinstance(object_info, dict):
            return
        for property_id, value in object_info.items():
            if hasattr(self, property_id) and value:
                setattr(self, property_id, value)

    def get_stats(self) -> dict[str, Any]:
        """Get aggregated statistics of calibration frames in the library.

        Covers dark, bias, and flat frames.



        Returns
        -------
        stats : `dict` [`str`, `Any`]
            Dict with ``"darks"``, ``"biases"``, and ``"flats"`` keys,
            each a list of per-group count summaries.
        """
        darks = []
        biases = []
        flats = []

        with self._lock:
            # 1. Dark frames stats
            for camera, iso_dict in self.dark_frames.items():
                if not isinstance(iso_dict, dict):
                    continue
                for iso, exp_dict in iso_dict.items():
                    if not isinstance(exp_dict, dict):
                        continue
                    for exp, file_list in exp_dict.items():
                        if not isinstance(file_list, list):
                            continue
                        try:
                            exposure = float(exp)
                        except ValueError, TypeError:
                            exposure = None
                        darks.append({
                            "camera": camera,
                            "iso": iso,
                            "exposure": exposure,
                            "count": len(file_list),
                        })

            # 2. Bias frames stats
            for camera, iso_dict in self.bias_frames.items():
                if not isinstance(iso_dict, dict):
                    continue
                for iso, file_list in iso_dict.items():
                    if not isinstance(file_list, list):
                        continue
                    biases.append({"camera": camera, "iso": iso, "count": len(file_list)})

            # 3. Flat frames stats
            for _telescope, camera_dict in self.flat_frames.items():
                if not isinstance(camera_dict, dict):
                    continue
                for camera, filter_dict in camera_dict.items():
                    if not isinstance(filter_dict, dict):
                        continue
                    for filt, iso_dict in filter_dict.items():
                        if not isinstance(iso_dict, dict):
                            continue
                        for iso, file_list in iso_dict.items():
                            if not isinstance(file_list, list):
                                continue
                            flats.append({
                                "camera": camera,
                                "filter": filt,
                                "iso": iso,
                                "count": len(file_list),
                            })

        return {"darks": darks, "biases": biases, "flats": flats}

    def _get_iso_gain(self, header):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Extract ISO or GAIN from header.

        Returns
        -------
        iso_or_gain : `str`
            The resolved ISO/GAIN value as a string.
        """
        # Nikon cameras frequently have inconsistent ISO headers;
        # assume 800 as per user requirement.
        instrume = str(header.get("INSTRUME", header.get("CAMERA", ""))).upper()
        if "NIKON" in instrume:
            return "800"

        iso = header.get("ISOSPEED")
        if iso is None:
            iso = header.get("GAIN")
        if iso is None:
            iso = "800"
        return str(iso)

    def _get_camera_name(self, header):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Normalize camera name from header.

        Returns
        -------
        camera_name : `str`
            Normalized camera name.
        """
        c_head = header.get("INSTRUME", header.get("CAMERA", "Unknown"))
        c_head = c_head.replace("ZWO CCD", "ZWO").replace("ASI533", "ASI 533")
        if c_head == "Unknown":
            return "ZWO ASI 533MM Pro"
        return c_head

    def add_dark_frame(self, image_file):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Add a dark frame to the library."""
        if not image_file.lower().endswith((".fits", ".fit")):
            return

        try:
            with fits.open(image_file) as hdu_list:
                header_info = hdu_list[0].header
                camera = self._get_camera_name(header_info)
                iso_speed = self._get_iso_gain(header_info)
                exposure_time = str(header_info.get("EXPTIME", "30.0"))

                with self._lock:
                    if camera not in self.dark_frames:
                        self.dark_frames[camera] = {}
                    if iso_speed not in self.dark_frames[camera]:
                        self.dark_frames[camera][iso_speed] = {}
                    if exposure_time not in self.dark_frames[camera][iso_speed]:
                        self.dark_frames[camera][iso_speed][exposure_time] = []

                    if image_file not in self.dark_frames[camera][iso_speed][exposure_time]:
                        self.dark_frames[camera][iso_speed][exposure_time].append(image_file)
        except Exception as e:
            logger.error(f"Error adding dark frame {image_file}: {e}")

    def add_bias_frame(self, image_file):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Add a bias frame to the library."""
        if not image_file.lower().endswith((".fits", ".fit")):
            return

        try:
            with fits.open(image_file) as hdu_list:
                header_info = hdu_list[0].header
                camera = self._get_camera_name(header_info)
                iso_speed = self._get_iso_gain(header_info)

                with self._lock:
                    if camera not in self.bias_frames:
                        self.bias_frames[camera] = {}
                    if iso_speed not in self.bias_frames[camera]:
                        self.bias_frames[camera][iso_speed] = []
                    if image_file not in self.bias_frames[camera][iso_speed]:
                        self.bias_frames[camera][iso_speed].append(image_file)
        except Exception as e:
            logger.error(f"Error adding bias frame {image_file}: {e}")

    def add_flat_frame(self, image_file, telescope="Apertura 75Q"):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Add a flat frame to the library."""
        if not image_file.lower().endswith((".fits", ".fit")):
            return

        from astrometricslib.image_processing.filter_detection import get_filter_type

        try:
            with fits.open(image_file) as hdu_list:
                header_info = hdu_list[0].header
                camera = self._get_camera_name(header_info)

                filter_type = get_filter_type(header_info)
                filter_val = filter_type.value if hasattr(filter_type, "value") else str(filter_type)
                iso_speed = self._get_iso_gain(header_info)

                with self._lock:
                    if telescope not in self.flat_frames:
                        self.flat_frames[telescope] = {}
                    if camera not in self.flat_frames[telescope]:
                        self.flat_frames[telescope][camera] = {}
                    if filter_val not in self.flat_frames[telescope][camera]:
                        self.flat_frames[telescope][camera][filter_val] = {}
                    if iso_speed not in self.flat_frames[telescope][camera][filter_val]:
                        self.flat_frames[telescope][camera][filter_val][iso_speed] = []

                    if image_file not in self.flat_frames[telescope][camera][filter_val][iso_speed]:
                        self.flat_frames[telescope][camera][filter_val][iso_speed].append(image_file)
        except Exception as e:
            logger.error(f"Error adding flat frame {image_file}: {e}")

    def check_for_calibration_frames(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        iso=None,  # ruff: ignore[missing-type-function-argument]
        exposure=None,  # ruff: ignore[missing-type-function-argument]
        camera="ZWO ASI 533MM Pro",  # ruff: ignore[missing-type-function-argument]
        telescope="Apertura 75Q",  # ruff: ignore[missing-type-function-argument]
        filter_type=None,  # ruff: ignore[missing-type-function-argument]
    ):
        """Check whether dark, bias, and flat frames all exist.

        Parameters
        ----------
        iso : `Any`, optional
            Unused; accepted for call-site compatibility, by default
            `None`.
        exposure : `Any`, optional
            Exposure time to look up dark frames for, by default
            `None`.
        camera : `str`, optional
            Camera name to look up frames for, by default
            ``"ZWO ASI 533MM Pro"``.
        telescope : `str`, optional
            Telescope name to look up flat frames for, by default
            ``"Apertura 75Q"``.
        filter_type : `Any`, optional
            Filter to look up flat frames for, by default `None`.

        Returns
        -------
        has_all_frames : `bool`
            `True` if at least one dark, bias, and flat frame each
            exist for the given parameters; `False` otherwise
            (including if the lookup itself raises).
        """
        try:
            darks = self.get_dark_frames(camera=camera, exposure=exposure)
            biases = self.get_bias_frames(camera=camera)
            flats = self.get_flat_frames(telescope=telescope, camera=camera, filter_type=filter_type)
            return bool(darks and biases and flats)
        except Exception:
            return False

    def _get_camera_dict(self, frame_dict: dict[str, Any], camera: str) -> dict[str, Any]:
        """Find camera data in `frame_dict`, allowing fuzzy matching.

        Returns
        -------
        camera_data : `dict`
            The matching camera's frame data, or an empty dict if no
            match is found.
        """
        if not camera or camera == "Unknown":
            # Fallback to first camera if only one exists
            if len(frame_dict) == 1:
                return next(iter(frame_dict.values()))
            return {}

        if camera in frame_dict:
            return frame_dict[camera]

        # Fuzzy match: "Nikon D5300" should match "Nikon DSLR DSC D5300"
        for key in frame_dict:
            if camera.upper() in key.upper() or key.upper() in camera.upper():
                return frame_dict[key]

        return {}

    def get_dark_frames(self, camera="ZWO ASI 533MM Pro", exposure=None, validate_paths=True, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-undocumented-public-function]
        """Retrieve dark frames for a camera and exposure.

        Returns
        -------
        frames : `list` [`str`]
            Matching dark frame file paths, filtered to existing
            files if `validate_paths` is `True`.
        """
        camera_data = self._get_camera_dict(self.dark_frames, camera)
        frames = []

        try:
            target_exp = float(exposure) if exposure is not None else None
        except ValueError, TypeError:
            target_exp = None

        # Iterate through all ISO keys (ignoring ISO)
        for iso_key in camera_data:
            iso_dict = camera_data[iso_key]
            # iso_dict is Dict[str, List[str]] where keys are exposure strings
            for exp_key, file_list in iso_dict.items():
                try:
                    # Fuzzy exposure match (allow 0.1s jitter)
                    if target_exp is None or abs(float(exp_key) - target_exp) < 0.1:
                        frames.extend(file_list)
                except ValueError, TypeError:
                    # Fallback to exact string match
                    if str(exposure) == exp_key:
                        frames.extend(file_list)

        if validate_paths:
            return [f for f in frames if os.path.exists(f)]
        return frames

    def get_bias_frames(self, camera="ZWO ASI 533MM Pro", validate_paths=True, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-undocumented-public-function]
        """Retrieve bias frames for a camera.

        Returns
        -------
        frames : `list` [`str`]
            Matching bias frame file paths, filtered to existing
            files if `validate_paths` is `True`.
        """
        camera_data = self._get_camera_dict(self.bias_frames, camera)
        frames = []

        # Gather from all ISO keys
        for iso_key in camera_data:
            iso_list = camera_data[iso_key]
            if isinstance(iso_list, list):
                frames.extend(iso_list)

        if validate_paths:
            return [f for f in frames if os.path.exists(f)]
        return frames

    def get_flat_frames(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        telescope="Apertura 75Q",  # ruff: ignore[missing-type-function-argument]
        camera="ZWO ASI 533MM Pro",  # ruff: ignore[missing-type-function-argument]
        filter_type=None,  # ruff: ignore[missing-type-function-argument]
        validate_paths=True,  # ruff: ignore[missing-type-function-argument]
        **kwargs,  # ruff: ignore[missing-type-kwargs]
    ):
        """Retrieve flat frames for a telescope, camera, and filter.

        Returns
        -------
        frames : `list` [`str`]
            Matching flat frame file paths, filtered to existing
            files if `validate_paths` is `True`.
        """
        f_val = filter_type.value if hasattr(filter_type, "value") else str(filter_type)

        # Filter aliases to handle terminology updates
        filter_aliases = {
            "L": ["Luminance", "L", "None", "NONE", ""],
            "Luminance": ["Luminance", "L", "None", "NONE", ""],
            "SPEC": ["Star Analyzer 200", "SPEC", "SA200"],
            "Star Analyzer 200": ["Star Analyzer 200", "SPEC", "SA200"],
            "None": ["None", "NONE", "", "Luminance", "L"],
            "NONE": ["None", "NONE", "", "Luminance", "L"],
            "": ["None", "NONE", "", "Luminance", "L"],
        }

        search_filters = filter_aliases.get(f_val, [f_val])

        telescope_data = self.flat_frames.get(telescope, {})
        if not telescope_data and len(self.flat_frames) > 0:
            # Fallback to the first telescope if not found, to
            # support offline stacking without telescope metadata
            telescope_data = next(iter(self.flat_frames.values()))

        camera_data = self._get_camera_dict(telescope_data, camera)

        frames = []
        for f_name in search_filters:
            filter_data = camera_data.get(f_name, {})
            # Gather from all ISO keys
            for iso_key in filter_data:
                iso_list = filter_data[iso_key]
                if isinstance(iso_list, list):
                    frames.extend(iso_list)

        if validate_paths:
            return [f for f in frames if os.path.exists(f)]
        return frames

    def refresh_dark_frames(self, prune_missing=False):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Rescan the dark-frames directory and re-add any FITS files found.

        Parameters
        ----------
        prune_missing : `bool`, optional
            If `True`, clear `dark_frames` before rescanning so
            entries for files that no longer exist are dropped, by
            default `False`.
        """
        if not self.app_config:
            return
        if prune_missing:
            self.dark_frames = {}

        image_file_path = self.app_config.get_frames_path() / "darks" / ""
        if not os.path.exists(image_file_path):
            return

        for root, _, files in os.walk(image_file_path):
            for file in files:
                file_path = os.path.join(root, file)
                if any(x in file_path for x in ["/Dark/", "/Bias/", "/Flat/"]):
                    continue
                if os.path.isfile(file_path):
                    self.add_dark_frame(file_path)

    def refresh_bias_frames(self, prune_missing=False):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Rescan the bias-frames directory and re-add any FITS files found.

        Parameters
        ----------
        prune_missing : `bool`, optional
            If `True`, clear `bias_frames` before rescanning so
            entries for files that no longer exist are dropped, by
            default `False`.
        """
        if not self.app_config:
            return
        if prune_missing:
            self.bias_frames = {}

        image_file_path = self.app_config.get_frames_path() / "biases" / ""
        if not os.path.exists(image_file_path):
            return

        for root, _, files in os.walk(image_file_path):
            for file in files:
                file_path = os.path.join(root, file)
                if any(x in file_path for x in ["/Dark/", "/Bias/", "/Flat/"]):
                    continue
                if os.path.isfile(file_path):
                    self.add_bias_frame(file_path)

    def refresh_flat_frames(self, prune_missing=False):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Rescan the flat-frames directory and re-add any FITS files found.

        Iterates each per-telescope subdirectory under the flats
        root, since flat frames are organized by telescope.

        Parameters
        ----------
        prune_missing : `bool`, optional
            If `True`, clear `flat_frames` before rescanning so
            entries for files that no longer exist are dropped, by
            default `False`.
        """
        if not self.app_config:
            return
        if prune_missing:
            self.flat_frames = {}

        base_flats_path = self.app_config.get_frames_path() / "flats" / ""
        if not os.path.exists(base_flats_path):
            return

        for telescope in os.listdir(base_flats_path):
            image_file_path = os.path.join(base_flats_path, telescope)
            if os.path.isdir(image_file_path):
                for root, _, files in os.walk(image_file_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        if any(x in file_path for x in ["/Dark/", "/Bias/", "/Flat/"]):
                            continue
                        if os.path.isfile(file_path):
                            self.add_flat_frame(file_path, telescope)
