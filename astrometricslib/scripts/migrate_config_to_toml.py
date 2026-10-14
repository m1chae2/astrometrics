r"""Purpose: One-time migration from the old INI config + JSON camera profiles.

Description: before this migration, camera facts lived in two places --
`astrometrics.config` (INI, per-setup facts like pixel size and grating
geometry) and `astrometricslib/instruments/cameras/*.json` (one file per
camera model, holding clip ceiling / saturation threshold / quantum
efficiency, each with a provenance note). This script merges both into a
single `astrometrics.config.toml`, so an existing installation upgrades
without hand-transcribing anything. Run once, by hand, after pulling the
change that removes the JSON profile files:

    python -m astrometricslib.scripts.migrate_config_to_toml \
        astrometricslib/astrometrics.config \
        astrometricslib/instruments/cameras \
        astrometricslib/astrometrics.config.toml

It is safe to run against a config that already lacks some or all camera
sections -- any camera profile with no matching config section gets a new
one of its own.
"""

import argparse
import configparser
import json
from pathlib import Path

import tomlkit

from astrometricslib.foundation.camera_names import normalize_camera_name

_CAMERA_SECTION_PREFIX = "Observatory.Camera."


def _load_camera_profiles(profiles_dir: Path) -> list[dict]:
    """Read every camera profile JSON file in `profiles_dir`.

    Returns
    -------
    profiles : `list` [`dict`]
        The parsed profile dictionaries, sorted by filename.
    """
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(profiles_dir.glob("*.json"))]


def _find_profile(profiles: list[dict], camera_name: str) -> dict | None:
    """Find the profile matching `camera_name` by name or alias.

    Returns
    -------
    profile : `dict` or `None`
        The matching profile, or `None` if no profile's `camera_name` or
        `name_aliases` normalizes to the same value as `camera_name`.
    """
    wanted = normalize_camera_name(camera_name)
    for profile in profiles:
        names = [profile["camera_name"], *profile.get("name_aliases", [])]
        if any(normalize_camera_name(name) == wanted for name in names):
            return profile
    return None


def _inline_provenanced(field: dict) -> tomlkit.items.InlineTable:
    """Build a one-line ``{value, kind, source}`` table from a profile field.

    Returns
    -------
    inline_table : `tomlkit.items.InlineTable`
        The value and its provenance as one inline table.
    """
    inline = tomlkit.inline_table()
    inline["value"] = field["value"]
    inline["kind"] = field["provenance"]["kind"]
    inline["source"] = field["provenance"]["source"]
    return inline


def _apply_profile_fields(table: tomlkit.items.Table, profile: dict) -> None:
    """Write `profile`'s model-level facts into `table` in place."""
    if profile.get("name_aliases"):
        table["name_aliases"] = ", ".join(profile["name_aliases"])
    if profile.get("record_name"):
        table["record_name"] = profile["record_name"]
    if profile.get("is_generic_fallback"):
        table["is_generic_fallback"] = "true"
    table["clip_ceiling_adu"] = _inline_provenanced(profile["clip_ceiling_adu"])
    table["saturation_threshold_adu"] = _inline_provenanced(profile["saturation_threshold_adu"])
    if profile.get("photometric_linearity_limit_adu"):
        table["photometric_linearity_limit_adu"] = _inline_provenanced(
            profile["photometric_linearity_limit_adu"]
        )
    if profile.get("quantum_efficiency"):
        quantum_efficiency = profile["quantum_efficiency"]
        inline = tomlkit.inline_table()
        inline["wavelength_nm"] = quantum_efficiency["wavelength_nm"]
        inline["quantum_efficiency_fraction"] = quantum_efficiency["quantum_efficiency_fraction"]
        inline["kind"] = quantum_efficiency["provenance"]["kind"]
        inline["source"] = quantum_efficiency["provenance"]["source"]
        table["quantum_efficiency"] = inline


def migrate(ini_path: Path, profiles_dir: Path, output_path: Path) -> None:
    """Merge the INI config and JSON profiles into one TOML config file."""
    old = configparser.ConfigParser()
    old.read(str(ini_path), encoding="utf-8")

    profiles = _load_camera_profiles(profiles_dir) if profiles_dir.is_dir() else []
    matched_profile_ids = set()
    doc = tomlkit.document()

    for section in old.sections():
        table = tomlkit.table()
        for key, value in old[section].items():
            table[key] = value
        if section.startswith(_CAMERA_SECTION_PREFIX) and section != "Observatory.Camera":
            camera_name = old[section].get("name") or section[len(_CAMERA_SECTION_PREFIX) :]
            profile = _find_profile(profiles, camera_name)
            if profile is not None:
                _apply_profile_fields(table, profile)
                matched_profile_ids.add(id(profile))
        doc[section] = table

    # Any profile with no matching config section (e.g. the generic
    # fallback, or a camera never added to this particular config) gets a
    # new section of its own.
    for profile in profiles:
        if id(profile) in matched_profile_ids:
            continue
        table = tomlkit.table()
        table["name"] = profile["camera_name"]
        _apply_profile_fields(table, profile)
        doc[f"{_CAMERA_SECTION_PREFIX}{profile['camera_name']}"] = table

    output_path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def main() -> None:
    """Parse arguments and run the migration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ini_path", type=Path, help="Existing astrometrics.config (INI) to read.")
    parser.add_argument("profiles_dir", type=Path, help="Directory of instruments/cameras/*.json profiles.")
    parser.add_argument("output_path", type=Path, help="Where to write the merged astrometrics.config.toml.")
    args = parser.parse_args()
    migrate(args.ini_path, args.profiles_dir, args.output_path)
    print(f"Wrote {args.output_path}")


if __name__ == "__main__":
    main()
