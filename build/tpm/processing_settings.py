"""Purpose: Prepare the settings file of a processing run.

Description: A stack job needs three things the display flow does not: the
commands that start Siril, GraXpert and Cosmic Clarity, and a setting that
makes the job really stack. This script edits the run's own settings file
(inside the throwaway folder, never the user's):

* ``skip_unchanged_stacks_enabled`` is set to ``false``. With it on, the app
  keeps a stack whose frames, settings and code are unchanged, and a repeat
  run would skip the whole stacking stage and measure nothing. The setting is
  in the throwaway file only.
* In the synthetic mode the plate solver gets an empty index folder (the
  made-up stars cannot be solved; it would wait minutes).
* In the synthetic mode the throwaway file has no program settings, so the
  three commands are copied from the user's settings file (read only): the
  Siril command, the GraXpert command and the Cosmic Clarity denoise path and
  switch. A real-snapshot file already holds them (it keeps the user's
  processing sections).

The edit is checked by parsing the file again.

Usage::

    .venv/bin/python build/tpm/processing_settings.py <sandbox> \
        --mode synthetic|real-snapshot
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

#: The setting that must be off so a repeat run stacks again.
SKIP_UNCHANGED = ("Processing.Siril", "skip_unchanged_stacks_enabled", "false")
#: Program settings copied from the user's file in the synthetic mode.
#: Settings of the made-up library only. Its stars are random dots, so the
#: plate solve of the stack can never succeed; an empty index folder makes
#: the solver fail at once instead of waiting out its 300 s limit twice.
SYNTHETIC_SETTINGS = (("Processing.Astrometry.Local Solver", "index_path", "/nonexistent-tpm-index"),)
COPIED_SETTINGS = (
    ("Processing.Siril", "siril_executable"),
    ("Processing.GraXpert", "graxpert_executable"),
    ("Processing.CosmicClarity", "denoise_executable"),
    ("Processing.CosmicClarity", "denoise_enabled"),
)


def set_value(text: str, section: str, key: str, value: str) -> str:
    """Set one key of one section in a settings text.

    Parameters
    ----------
    text : `str`
        The settings file's text (TOML with quoted section names, such as
        ``["Processing.Siril"]``).
    section : `str`
        The section name.
    key : `str`
        The key.
    value : `str`
        The value; it is written as a quoted string, as the app does.

    Returns
    -------
    new_text : `str`
        The text with the key set: the line replaced if the key is there, the
        line added under the header if only the section is, or a new section
        added at the end.
    """
    header = re.compile(rf'(?m)^\["?{re.escape(section)}"?\]\s*$')
    line = f'{key} = "{value}"'
    found = header.search(text)
    if found is None:
        return text.rstrip("\n") + f'\n\n["{section}"]\n{line}\n'
    block_end = re.search(r"(?m)^\[", text[found.end() :])
    end = found.end() + block_end.start() if block_end else len(text)
    block = text[found.end() : end]
    existing = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
    if existing.search(block):
        block = existing.sub(line, block, count=1)
    else:
        block = f"\n{line}{block}"
    return text[: found.end()] + block + text[end:]


def read_real_settings(path: Path) -> dict:
    """Read the user's settings file, read only.

    Parameters
    ----------
    path : `pathlib.Path`
        The user's file.

    Returns
    -------
    settings : `dict`
        The parsed file; empty if it cannot be read.
    """
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, tomllib.TOMLDecodeError:
        return {}


def apply(config_path: Path, mode: str, real_settings: dict | None = None) -> dict[str, str]:
    """Edit the throwaway settings file for a processing run.

    Parameters
    ----------
    config_path : `pathlib.Path`
        The throwaway settings file.
    mode : `str`
        ``synthetic`` or ``real-snapshot``.
    real_settings : `dict`, optional
        The user's parsed settings (the synthetic mode copies from it).

    Returns
    -------
    applied : `dict` [`str`, `str`]
        ``section.key`` to the value now in the file, for the keys set.

    Raises
    ------
    RuntimeError
        If a key does not read back with the value written.
    """
    text = config_path.read_text(encoding="utf-8")
    wanted = [SKIP_UNCHANGED]
    if mode == "synthetic":
        wanted.extend(SYNTHETIC_SETTINGS)
        for section, key in COPIED_SETTINGS:
            value = (real_settings or {}).get(section, {}).get(key)
            if value is not None:
                wanted.append((section, key, str(value)))
    for section, key, value in wanted:
        text = set_value(text, section, key, value)
    parsed = tomllib.loads(text)
    for section, key, value in wanted:
        if str(parsed.get(section, {}).get(key)) != value:
            raise RuntimeError(f"{section}.{key} did not read back as {value!r}")
    config_path.write_text(text, encoding="utf-8")
    return {f"{section}.{key}": value for section, key, value in wanted}


def main() -> int:
    """Edit the settings file inside a throwaway folder.

    Returns
    -------
    status : `int`
        0 on success, 2 if the settings file is not inside the folder.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sandbox", type=Path)
    parser.add_argument("--mode", choices=("synthetic", "real-snapshot"), required=True)
    arguments = parser.parse_args()
    sandbox = arguments.sandbox.resolve()
    config_path = sandbox / "astrometrics.config.toml"
    if not config_path.is_file():
        print(f"ERROR: {config_path} does not exist.", file=sys.stderr)
        return 2
    real_settings = None
    if arguments.mode == "synthetic":
        from simulator.real_folders import real_folders

        real_settings = read_real_settings(real_folders().config_file)
    applied = apply(config_path, arguments.mode, real_settings)
    for name, value in applied.items():
        print(f"  {name} = {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
