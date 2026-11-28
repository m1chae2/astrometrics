"""Purpose: Shared folders and safety checks of the plate-solver study.

Description: Every script of the study works inside one study folder given on
the command line (``--study``). The folder holds ``frames/`` (copies of the
library frames with their old plate solutions removed), ``work/`` (temporary
files and Siril's private settings file) and ``out/`` (the results). This
module builds those paths and refuses a study folder that lies inside an
image library, so no script of the study can write into the library. A
folder counts as a library when it is the library named by ``--library`` or
in the study's manifest, or when it holds both a ``lights`` and a ``stacks``
folder, the layout of an Astrometrics library.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The repository root, four levels up from this file
# (build/agent_tools/plate_solver_study/study_common.py), so the scripts
# import the checkout they sit in.
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

# FITS cards that make up a plate solution (the sky map and its SIP
# distortion terms). The study strips them from the frame copies and saves
# them from each solver's output.
WCS_CARD_PATTERN = re.compile(
    r"^(CTYPE|CRVAL|CRPIX|CD\d_|PC\d_|CDELT|CROTA|CUNIT|A_|B_|AP_|BP_|LONPOLE|LATPOLE|WCSAXES|PLTSOLVD|"
    r"PV\d_|IMAGEW|IMAGEH|WCSNAME|MJDREF|RADESYS|RADECSYS|EQUINOX)"
)


@dataclass(frozen=True)
class StudyFolder:
    """The folders and files of one study.

    Attributes
    ----------
    root : `pathlib.Path`
        The study folder.
    frames : `pathlib.Path`
        Copies of the study frames, with their plate solutions removed.
    work : `pathlib.Path`
        Temporary files and Siril's private settings file.
    out : `pathlib.Path`
        The results: manifest, run log, judgements and tables.
    """

    root: Path
    frames: Path
    work: Path
    out: Path

    @property
    def manifest_path(self) -> Path:
        """The path of the frame manifest.

        Returns
        -------
        path : `pathlib.Path`
            ``out/manifest.json``.
        """
        return self.out / "manifest.json"

    @property
    def siril_settings(self) -> Path:
        """The path of Siril's private settings file.

        Returns
        -------
        path : `pathlib.Path`
            ``work/siril_study.ini``.
        """
        return self.work / "siril_study.ini"

    def read_manifest(self) -> dict[str, dict]:
        """Read the frame manifest written by ``prep_frames.py``.

        Returns
        -------
        rows : `dict` [`str`, `dict`]
            One row of header cards per frame, keyed by the frame's study id.
        """
        return {row["id"]: row for row in json.loads(self.manifest_path.read_text())}


def _is_inside(path: Path, folder: Path) -> bool:
    """Say whether a path is a folder or lies inside it.

    Parameters
    ----------
    path : `pathlib.Path`
        The path to test, already resolved.
    folder : `pathlib.Path`
        The folder, already resolved.

    Returns
    -------
    inside : `bool`
        True when ``path`` is ``folder`` or below it.
    """
    return path == folder or folder in path.parents


def _looks_like_library(folder: Path) -> bool:
    """Say whether a folder has the layout of an image library.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder to test.

    Returns
    -------
    is_library : `bool`
        True when the folder holds both a ``lights`` and a ``stacks`` folder.
    """
    return (folder / "lights").is_dir() and (folder / "stacks").is_dir()


def refuse_library_folder(study_root: Path, libraries: list[Path]) -> None:
    """Stop the program when the study folder lies inside a library.

    Parameters
    ----------
    study_root : `pathlib.Path`
        The study folder, already resolved.
    libraries : `list` [`pathlib.Path`]
        Library folders the study must stay out of.
    """
    for library in libraries:
        if _is_inside(study_root, library.resolve()):
            sys.exit(f"Refusing: the study folder {study_root} lies inside the library {library}.")
    for folder in (study_root, *study_root.parents):
        if _looks_like_library(folder):
            sys.exit(
                f"Refusing: the study folder {study_root} lies inside {folder}, which looks like a library."
            )


def open_study(study_path: str, library: str | None = None) -> StudyFolder:
    """Check the study folder and create its sub-folders.

    Parameters
    ----------
    study_path : `str`
        The study folder from the command line.
    library : `str` or `None`
        The library the frames come from, when the caller knows it.

    Returns
    -------
    study : `StudyFolder`
        The checked study folder.
    """
    root = Path(study_path).expanduser().resolve()
    libraries = [Path(library).expanduser()] if library else []
    manifest = root / "out" / "manifest.json"
    if manifest.exists():
        libraries += [
            Path(row["library"]) for row in json.loads(manifest.read_text())[:1] if row.get("library")
        ]
    refuse_library_folder(root, libraries)
    study = StudyFolder(root=root, frames=root / "frames", work=root / "work", out=root / "out")
    for folder in (study.frames, study.work, study.out, study.out / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    return study


def add_study_argument(parser: argparse.ArgumentParser) -> None:
    """Add the ``--study`` option every study script takes.

    Parameters
    ----------
    parser : `argparse.ArgumentParser`
        The script's argument parser.
    """
    parser.add_argument(
        "--study",
        required=True,
        help="The study folder, which holds every file the script writes. A library folder is refused.",
    )


def use_repository_code() -> None:
    """Put the checkout this script sits in first on the import path."""
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
