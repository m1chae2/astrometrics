"""Purpose: Unit tests for the two-folder static file server.

Description: The image viewer asks for ``lights/<target>/<file>`` whether the
file is a raw frame (frames folder) or a stack (stacks folder). These tests
check that the server finds a file in the frames folder first, falls back to
the stacks folder, and answers 404 when neither has it.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.infrastructure.fallback_static_files import FallbackStaticFiles


def _client(frames: Path, stacks: Path | None) -> TestClient:
    """Build a test client with the server mounted.

    Returns
    -------
    client : `TestClient`
        A client for an app serving `frames`, with `stacks` as the fallback.
    """
    app = FastAPI()
    app.mount(
        "/static/frames",
        FallbackStaticFiles(directory=str(frames), fallback_directory=str(stacks) if stacks else None),
    )
    return TestClient(app)


def test_a_file_in_the_frames_folder_is_served(tmp_path: Path) -> None:
    """The primary folder is looked in first."""
    (tmp_path / "frames" / "lights" / "M 27").mkdir(parents=True)
    (tmp_path / "frames" / "lights" / "M 27" / "frame.fits").write_bytes(b"raw")
    (tmp_path / "stacks").mkdir()

    response = _client(tmp_path / "frames", tmp_path / "stacks").get("/static/frames/lights/M 27/frame.fits")

    assert response.status_code == 200
    assert response.content == b"raw"


def test_a_file_only_in_the_stacks_folder_is_served(tmp_path: Path) -> None:
    """A stack on the other disk is found through the fallback."""
    (tmp_path / "frames").mkdir()
    (tmp_path / "stacks" / "lights" / "M 27").mkdir(parents=True)
    (tmp_path / "stacks" / "lights" / "M 27" / "M_27_Stacked.fits").write_bytes(b"stack")

    response = _client(tmp_path / "frames", tmp_path / "stacks").get(
        "/static/frames/lights/M 27/M_27_Stacked.fits"
    )

    assert response.status_code == 200
    assert response.content == b"stack"


def test_a_file_in_neither_folder_is_a_404(tmp_path: Path) -> None:
    """A missing file is still reported missing."""
    (tmp_path / "frames").mkdir()
    (tmp_path / "stacks").mkdir()

    assert (
        _client(tmp_path / "frames", tmp_path / "stacks").get("/static/frames/nothing.fits").status_code
        == 404
    )


def test_without_a_fallback_only_the_frames_folder_is_used(tmp_path: Path) -> None:
    """With no stacks folder set, nothing else is searched."""
    (tmp_path / "frames").mkdir()

    assert _client(tmp_path / "frames", None).get("/static/frames/x.fits").status_code == 404


def test_a_missing_frames_folder_serves_stacks_and_answers_404_for_the_rest(tmp_path: Path) -> None:
    """With the frames drive missing, stacks still load."""
    (tmp_path / "stacks" / "lights" / "M 27").mkdir(parents=True)
    (tmp_path / "stacks" / "lights" / "M 27" / "M_27_Stacked.fits").write_bytes(b"stack")
    client = _client(tmp_path / "missing_drive", tmp_path / "stacks")

    assert client.get("/static/frames/lights/M 27/M_27_Stacked.fits").status_code == 200
    assert client.get("/static/frames/lights/M 27/frame.fits").status_code == 404


def test_the_frames_folder_is_served_once_it_appears(tmp_path: Path) -> None:
    """A drive mounted after the server started works without a restart."""
    frames = tmp_path / "drive" / "frames"
    client = _client(frames, None)
    assert client.get("/static/frames/lights/M 27/frame.fits").status_code == 404

    (frames / "lights" / "M 27").mkdir(parents=True)
    (frames / "lights" / "M 27" / "frame.fits").write_bytes(b"raw")

    assert client.get("/static/frames/lights/M 27/frame.fits").status_code == 200
