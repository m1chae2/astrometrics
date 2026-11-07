"""Test that frame prefetch worker threads carry the caller's job context."""

from pathlib import Path

import pytest

from astrometricslib._internal.drivers import frame_prefetch
from astrometricslib.foundation.logging import active_job_ids, log_context


def test_prefetch_workers_carry_the_job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read done on a worker thread sees the job id of the caller."""
    seen: list[tuple[str, ...]] = []

    def fake_read(path: str, chunk_bytes: int) -> int:
        """Record the job ids seen on the worker thread.

        Returns
        -------
        byte_count : `int`
            Always 0.
        """
        seen.append(active_job_ids())
        return 0

    monkeypatch.setattr(frame_prefetch, "_read_whole_file", fake_read)
    frame = tmp_path / "frame.fits"
    frame.write_bytes(b"x")
    with log_context(job_id="job-1"):
        frame_prefetch.prefetch_frames([str(frame)], worker_count=2)
    assert seen == [("job-1",)]
