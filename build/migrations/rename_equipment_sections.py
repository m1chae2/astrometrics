"""Purpose: Rename old equipment sections in a configuration file.

Description: Older configuration files name the equipment sections
``[Telescope]``, ``[Telescope.<name>]``, ``[Camera]`` and ``[Camera.<name>]``.
The code reads only the current names, ``[Observatory.Telescope...]`` and
``[Observatory.Camera...]``. This script renames the old sections in place and
writes a ``.bak`` copy of the original file first.

Usage::

    python build/migrations/rename_equipment_sections.py path/to/config.ini
"""

import argparse
import re
import shutil
from pathlib import Path

#: Matches an old section header and captures its kind and optional name.
_OLD_SECTION = re.compile(r"^\[(?P<kind>Telescope|Camera)(?P<rest>(?:\.[^\]]+)?)\]\s*$")


def rename_sections(text: str) -> tuple[str, int]:
    """Rename the old equipment section headers in configuration text.

    Parameters
    ----------
    text : `str`
        The full text of a configuration file.

    Returns
    -------
    new_text : `str`
        The text with every old header renamed.
    count : `int`
        How many headers changed.
    """
    count = 0
    lines = []
    for line in text.splitlines(keepends=True):
        match = _OLD_SECTION.match(line.rstrip("\r\n"))
        if match:
            ending = line[len(line.rstrip("\r\n")) :]
            line = f"[Observatory.{match['kind']}{match['rest']}]{ending}"
            count += 1
        lines.append(line)
    return "".join(lines), count


def main() -> None:
    """Rename the old sections in the file named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_path", type=Path, help="The configuration file to migrate.")
    args = parser.parse_args()
    text = args.config_path.read_text(encoding="utf-8")
    new_text, count = rename_sections(text)
    if count == 0:
        print("No old equipment sections found.")
        return
    shutil.copy2(args.config_path, args.config_path.with_suffix(args.config_path.suffix + ".bak"))
    args.config_path.write_text(new_text, encoding="utf-8")
    print(f"Renamed {count} section(s). The original is saved with a .bak suffix.")


if __name__ == "__main__":
    main()
