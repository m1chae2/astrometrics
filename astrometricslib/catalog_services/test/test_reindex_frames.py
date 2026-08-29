"""Tests for target_records.reindex_frames's filesystem scan.

Was previously reached through CatalogAccess.get("raw_frames", ...) --
scanning a target's frame folder is not a database read, so that
dataset type moved out of CatalogAccess and this now calls
reindex_frames directly.
"""

from unittest.mock import MagicMock

from astrometricslib import Target
from astrometricslib.catalog_services.target_records import reindex_frames
from astrometricslib.data_access.catalog_access import CatalogAccess
from astrometricslib.models.target import FrameRecord


def test_reindex_frames_skips_already_tracked_frames(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify reindex_frames re-parses only new, untracked frames."""
    lights_dir = tmp_path / "lights" / "TestTarget"
    lights_dir.mkdir(parents=True)
    known_file = lights_dir / "known.fits"
    known_file.write_text("")
    new_file = lights_dir / "new.fits"
    new_file.write_text("")

    mock_config = MagicMock()
    mock_config.get_frames_path.return_value = str(tmp_path)
    catalog_access = CatalogAccess(config=mock_config)

    target = Target(id="TestTarget", frames=[FrameRecord(path=str(known_file))])

    mock_create_record = mocker.patch(
        "astrometricslib.catalog_services.frame_scanning.create_frame_record_from_fits",
        side_effect=lambda path, camera=None: FrameRecord(path=path),
    )

    reindex_frames(target, catalog_access=catalog_access)

    assert mock_create_record.call_count == 1
    assert mock_create_record.call_args[0][0] == str(new_file)
    assert {f.path for f in target.frames} == {str(known_file), str(new_file)}
