"""Purpose: Unit tests for reading Siril's sequence (.seq) file.

Description: Checks `parse_seq_file` and `parse_seq_inclusion` against lines
copied from a real M 106 stack. Siril's own reader (``src/io/seqfile.c``)
defines the registration line as ``R<layer> fwhm weighted_fwhm roundness
quality background_level number_of_stars H h00 ... h22``.
"""

from pathlib import Path

import pytest

from astrometricslib.drivers.siril_output_parsing import parse_seq_file, parse_seq_inclusion

M106_REGISTRATION_LINE = (
    "R1 2.83064 3.27475 0.886847 0 0.0238392 1163 H 1 -1.53863e-05 6.3825 1.73631e-05 "
    "1.00003 -29.2903 -2.69105e-09 4.67203e-09 1"
)


def test_registration_line_is_read_by_its_real_fields(tmp_path: Path) -> None:
    """Each number of a real registration line lands under its true name."""
    seq_path = tmp_path / "light_Registration.seq"
    seq_path.write_text(f"I 0 1\n{M106_REGISTRATION_LINE}\n")

    (frame,) = parse_seq_file(str(seq_path))

    assert frame["fwhm_px"] == pytest.approx(2.83064)
    assert frame["weighted_fwhm_px"] == pytest.approx(3.27475)
    assert frame["roundness"] == pytest.approx(0.886847)
    assert frame["quality"] == pytest.approx(0.0)
    assert frame["background_level"] == pytest.approx(0.0238392)
    assert frame["star_count"] == 1163
    assert frame["dx"] == pytest.approx(6.3825)
    assert frame["dy"] == pytest.approx(-29.2903)
    assert frame["rotation_deg"] == pytest.approx(0.000995, abs=1e-5)
    assert frame["scale"] == pytest.approx(1.000015, abs=1e-5)
    assert len(frame["homography"]) == 9
    assert "rmse" not in frame


def test_a_missing_sequence_file_gives_no_frames(tmp_path: Path) -> None:
    """A path that does not exist reads as an empty list."""
    assert parse_seq_file(str(tmp_path / "none.seq")) == []


def test_image_line_marks_the_frame_as_included(tmp_path: Path) -> None:
    """An ``I 0 1`` line is an included image and ``I 1 0`` a dropped one."""
    seq_path = tmp_path / "light.seq"
    seq_path.write_text("I 0 1\nI 1 0\n")

    assert parse_seq_inclusion(str(seq_path)) == [True, False]
