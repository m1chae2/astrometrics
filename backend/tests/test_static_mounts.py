"""Purpose: Unit tests for the path-to-URL table of the served folders.

Description: A browser or phone client cannot guess where the backend serves
a file. These tests check `static_mounts` and the mounts built from it for the
real layout (frames, stacks and library on different folders) and for the
sandbox layout (all three inside one library folder).
"""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.routing import Mount

from backend.routers.static_files import mount_library_files
from backend.services.infrastructure.static_mounts import static_mounts


def _configuration(library: Path, frames: Path, stacks: Path) -> SimpleNamespace:
    """Build a stand-in configuration that names the three folders.

    Returns
    -------
    configuration : `~types.SimpleNamespace`
        An object with the three ``get_*_path`` methods.
    """
    return SimpleNamespace(
        get_library_path=lambda: library, get_frames_path=lambda: frames, get_stacks_path=lambda: stacks
    )


def _mounted_prefixes(app: FastAPI) -> dict[str, list[str]]:
    """Read the folders each mounted URL prefix serves.

    Returns
    -------
    mounted : `dict` [`str`, `list` [`str`]]
        For each prefix, the primary folder and the fallback folder if any.
    """
    mounted: dict[str, list[str]] = {}
    for route in app.routes:
        if isinstance(route, Mount):
            folders = [str(route.app.directory)]
            fallback = getattr(route.app, "_fallback", None)
            if fallback is not None:
                folders.append(str(fallback.directory))
            mounted[route.path] = folders
    return mounted


def test_real_layout_mounts_exactly_what_the_table_lists(tmp_path: Path) -> None:
    """Every table entry is mounted, and nothing else is."""
    (tmp_path / "library").mkdir()
    configuration = _configuration(tmp_path / "library", tmp_path / "frames", tmp_path / "stacks")
    app = FastAPI()
    mount_library_files(app, configuration)
    mounted = _mounted_prefixes(app)
    expected: dict[str, list[str]] = {}
    for mount in static_mounts(configuration):
        expected.setdefault(mount["urlPrefix"], []).append(mount["root"])
    assert mounted == expected
    assert list(mounted) == ["/static/frames", "/static"]


def test_sandbox_layout_mounts_exactly_what_the_table_lists(tmp_path: Path) -> None:
    """With frames equal to the library folder only one mount exists."""
    configuration = _configuration(tmp_path, tmp_path, tmp_path)
    app = FastAPI()
    mount_library_files(app, configuration)
    assert _mounted_prefixes(app) == {"/static": [str(tmp_path)]}


def test_stacks_folder_is_listed_under_the_frames_prefix() -> None:
    """A separate stacks folder shares the frames prefix."""
    configuration = _configuration(Path("/data/library"), Path("/mnt/frames"), Path("/home/u/stacks"))
    assert static_mounts(configuration) == [
        {"root": "/data/library", "urlPrefix": "/static"},
        {"root": "/mnt/frames", "urlPrefix": "/static/frames"},
        {"root": "/home/u/stacks", "urlPrefix": "/static/frames"},
    ]


def test_frames_folder_equal_to_library_has_one_mount() -> None:
    """With no separate frames folder, only the library mount is listed."""
    library = Path("/data/library")
    assert static_mounts(_configuration(library, library, library)) == [
        {"root": "/data/library", "urlPrefix": "/static"}
    ]
