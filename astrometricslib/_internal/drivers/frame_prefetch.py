"""Reads a target's frame files ahead of time so they are in the local cache.

Raw frames can live on a network drive that is mounted as a folder. The mount
keeps a local disk cache. A file that is not in the cache yet is downloaded
the first time something reads it. Siril reads a target's frames one after
another, so on a cold cache each frame waits for its own download. Reading
several files at the same time is much faster, because the downloads overlap.

This step reads every file all the way through, with a few readers running at
once, and throws the bytes away. The only effect is that the mount's cache now
holds the files, so Siril finds them on local disk.

The step is optional and off by default (``prefetch_frames`` in
``[Processing]``), because it downloads files. A failed read is logged and
counted, never raised: Siril will report the real error for that file later.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_CHUNK_BYTES", "PrefetchSummary", "prefetch_frames"]

# Bytes per read call. 4 MiB is a size chosen to keep the number of read calls
# small for a 10-30 MB frame without holding much memory per reader. It is not
# tuned; the mount's own read-ahead setting matters more.
DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024

# Progress is logged each time this fraction of the files has been read. Only
# sets of at least PROGRESS_MINIMUM_FILES files log progress, so a small stack
# stays quiet. Both are presentation choices, not measured values.
PROGRESS_FRACTION = 0.10
PROGRESS_MINIMUM_FILES = 20


@dataclass(frozen=True)
class PrefetchSummary:
    """What a pre-fetch pass did.

    Attributes
    ----------
    file_count : `int`
        Files read all the way through.
    byte_count : `int`
        Bytes read from those files.
    failure_count : `int`
        Files that could not be read (an error part-way counts as a failure).
    skipped_missing_count : `int`
        Paths skipped because no file exists there.
    elapsed_seconds : `float`
        Wall-clock time the pass took.
    """

    file_count: int
    byte_count: int
    failure_count: int
    skipped_missing_count: int
    elapsed_seconds: float


def _read_whole_file(path: str, chunk_bytes: int) -> int:
    """Read one file to its end and discard the bytes.

    Parameters
    ----------
    path : `str`
        File to read.
    chunk_bytes : `int`
        Bytes per read call.

    Returns
    -------
    byte_count : `int`
        Number of bytes read.
    """
    total = 0
    with open(path, "rb", buffering=0) as handle:
        while chunk := handle.read(chunk_bytes):
            total += len(chunk)
    return total


def prefetch_frames(
    frame_paths: list[str], worker_count: int, chunk_bytes: int = DEFAULT_CHUNK_BYTES
) -> PrefetchSummary:
    """Read every existing file in parallel so the mount's cache holds it.

    Parameters
    ----------
    frame_paths : `list` of `str`
        Files to read. Paths with no file are skipped and counted.
    worker_count : `int`
        Number of files read at the same time. Values below 1 are treated as 1.
    chunk_bytes : `int`, optional
        Bytes per read call, by default `DEFAULT_CHUNK_BYTES`.

    Returns
    -------
    summary : `PrefetchSummary`
        Counts of files, bytes, failures and skipped paths, and the time taken.
        Read errors are logged and counted, never raised.
    """
    started_at = time.monotonic()
    existing_paths = [path for path in frame_paths if os.path.isfile(path)]
    skipped_missing_count = len(frame_paths) - len(existing_paths)
    file_count = 0
    byte_count = 0
    failure_count = 0
    step = max(1, int(len(existing_paths) * PROGRESS_FRACTION))
    report_progress = len(existing_paths) >= PROGRESS_MINIMUM_FILES

    with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
        futures = {executor.submit(_read_whole_file, path, chunk_bytes): path for path in existing_paths}
        finished = 0
        for future in as_completed(futures):
            finished += 1
            try:
                byte_count += future.result()
                file_count += 1
            except OSError as error:
                failure_count += 1
                logger.warning("Pre-fetch could not read %s: %s", futures[future], error)
            if report_progress and finished % step == 0:
                logger.info("Pre-fetch: %d of %d files read.", finished, len(existing_paths))

    return PrefetchSummary(
        file_count=file_count,
        byte_count=byte_count,
        failure_count=failure_count,
        skipped_missing_count=skipped_missing_count,
        elapsed_seconds=time.monotonic() - started_at,
    )
