r"""Purpose: Write today's equipment defaults into the config for one setup.

Description: Issue #175 Phase C moved 67 values from the code into the
equipment sections of ``astrometrics.config.toml``. Their code defaults were
chosen on one setup, so this script writes those defaults into the config for
that setup: results do not change, and no run reports the values as missing.
A value already in the config is left as it is. It can also name the active
setup, and the setup's mount and filters.

It reads the declarations of both libraries, so it lives here rather than in
``astrometricslib`` (which must not import ``wayfindinglib``).

Without ``--write`` it only prints what it would add::

    .venv/bin/python build/config/write_equipment_parameters.py \
        --setup "ASI533 on Apertura" --active --mount "My Mount" \
        --filters "L, SPEC"
    .venv/bin/python build/config/write_equipment_parameters.py ... --write
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from astrometricslib import get_configuration  # ruff: ignore[module-import-not-at-top-of-file]
from astrometricslib.parameters import (  # ruff: ignore[module-import-not-at-top-of-file]
    collect_parameters,
    equipment_values_to_write,
)


def build_changes(
    setup_name: str, make_active: bool, mount: str | None, filters: str | None
) -> dict[str, dict[str, str]]:
    """Work out every section and key the script would add.

    Parameters
    ----------
    setup_name : `str`
        The setup listed under ``[Observatory.Setups]``.
    make_active : `bool`
        Whether to name it as the active setup.
    mount : `str` or `None`
        The mount to name on the setup, if any.
    filters : `str` or `None`
        The comma-separated filters to name on the setup, if any.

    Returns
    -------
    changes : `dict` [`str`, `dict` [`str`, `str`]]
        ``{section: {key: value}}``.

    Raises
    ------
    SystemExit
        If the config lists no setup of that name.
    """
    configuration = get_configuration()
    setups = configuration.get_observatory_setups()
    setup = setups.setup_named(setup_name)
    if setup is None:
        raise SystemExit(f"The config lists no setup named {setup_name!r}.")

    changes: dict[str, dict[str, str]] = {}
    setup_section = f"Observatory.Setup.{setup_name}"
    if make_active:
        changes.setdefault("Observatory.Setups", {})["active"] = setup_name
    if mount:
        changes.setdefault(setup_section, {})["mount"] = mount
        setup = setup.model_copy(update={"mount_name": mount})
    if filters:
        names = tuple(name.strip() for name in filters.split(",") if name.strip())
        changes.setdefault(setup_section, {})["filters"] = ", ".join(names)
        setup = setup.model_copy(update={"filter_names": names})

    parameters = [
        parameter
        for library in ("astrometricslib", "wayfindinglib")
        for parameter in collect_parameters(library).all()
    ]
    for section, values in equipment_values_to_write(parameters, configuration, setup).items():
        changes.setdefault(section, {}).update(values)
    return changes


def main(argv: list[str] | None = None) -> int:
    """Print, and with ``--write`` save, the values to add.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; defaults to ``sys.argv``.

    Returns
    -------
    exit_code : `int`
        0 on success.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--setup", required=True, help="The setup to write the values for.")
    parser.add_argument("--active", action="store_true", help="Name this setup as the active one.")
    parser.add_argument("--mount", help="The mount to name on the setup.")
    parser.add_argument("--filters", help="The comma-separated filters to name on the setup.")
    parser.add_argument("--write", action="store_true", help="Save the changes to the config file.")
    args = parser.parse_args(argv)

    changes = build_changes(args.setup, args.active, args.mount, args.filters)
    count = sum(len(values) for values in changes.values())
    for section, values in changes.items():
        print(f"[{section}]")
        for key, value in values.items():
            print(f'  "{key}" = "{value}"')
    if not args.write:
        print(f"{count} values to add. Nothing written; add --write to save them.")
        return 0
    get_configuration().update_config(changes)
    print(f"Wrote {count} values.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
