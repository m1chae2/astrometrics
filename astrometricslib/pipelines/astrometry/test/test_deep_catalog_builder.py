"""Tests for downloading the deep-star catalog.

The download runs for hours against a remote archive that sometimes fails, so
what matters most is that it can be stopped and resumed without losing or
duplicating anything, and that a bad chunk never gets saved as if it were
good. Every test here runs offline against a fake archive.
"""

import re
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.table import MaskedColumn, Table

from astrometricslib.drivers import deep_star_store
from astrometricslib.pipelines.astrometry import deep_catalog_builder
from astrometricslib.pipelines.astrometry.deep_catalog_builder import (
    build_deep_star_catalog,
    build_pixel_query,
    estimate_deep_catalog_size,
    healpix_pixels_of_points,
    pixel_source_id_range,
    pixels_near_circles,
)

# Level 0 cuts the sky into 12 chunks, the fewest possible, which keeps the
# tests short while still exercising the same code as level 4.
LEVEL = 0
PIXEL_COUNT = 12
ID_RANGE_PATTERN = re.compile(r"source_id >= (\d+) AND source_id < (\d+)")


class _LibraryConfig:
    """A stand-in for `AppConfiguration` that points at a temporary folder."""

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path

    def get_library_path(self) -> Path:
        """Return the sandboxed library root.

        Returns
        -------
        path : `pathlib.Path`
            The temporary directory standing in for the library.
        """
        return self._library_path


class _FakeArchive:
    """A stand-in for the Gaia archive that serves stars for any ID range.

    Parameters
    ----------
    stars_per_pixel : `int`
        How many stars to invent for every requested chunk.
    fail_pixels : `set` [`int`]
        Chunks (by their ID range's start) that always raise an error.
    """

    def __init__(self, stars_per_pixel: int = 3, fail_range_starts: set[int] | None = None) -> None:
        self.stars_per_pixel = stars_per_pixel
        self.fail_range_starts = fail_range_starts or set()
        self.queries: list[str] = []
        self.calls_by_range_start: dict[int, int] = {}
        self.fail_first_calls = 0
        self.hang_event: threading.Event | None = None
        self.return_none = False
        self.rows_override: Table | None = None

    def launch_job_async(self, query: str, **_keyword_arguments: Any) -> _FakeArchive._Job:
        """Record the query and hand back a job that answers it.

        Returns
        -------
        job : `_FakeArchive._Job`
            A job whose ``get_results`` gives the invented table.
        """
        self.queries.append(query)
        return _FakeArchive._Job(self, query)

    class _Job:
        def __init__(self, archive: _FakeArchive, query: str) -> None:
            self._archive = archive
            self._query = query

        def get_results(self) -> Table | None:
            """Invent the table for this job's query, or fail as configured.

            Returns
            -------
            table : `astropy.table.Table` or `None`
                The invented stars, or a count table for a count query.

            Raises
            ------
            RuntimeError
                If this chunk is set to fail.
            """
            archive = self._archive
            if archive.hang_event is not None:
                archive.hang_event.wait()
            low, _high = (int(value) for value in ID_RANGE_PATTERN.search(self._query).groups())
            archive.calls_by_range_start[low] = archive.calls_by_range_start.get(low, 0) + 1
            if low in archive.fail_range_starts:
                raise RuntimeError("Error 408: archive timed out")
            if archive.fail_first_calls > 0:
                archive.fail_first_calls -= 1
                raise RuntimeError("Error 500: temporary failure")
            if archive.return_none:
                return None
            if archive.rows_override is not None:
                return archive.rows_override
            if "COUNT(*)" in self._query:
                return Table({"star_count": [archive.stars_per_pixel]})
            count = archive.stars_per_pixel
            return Table({
                "source_id": low + np.arange(count, dtype=np.int64),
                "ra": np.linspace(100.0, 101.0, count),
                "dec": np.linspace(10.0, 11.0, count),
                "phot_g_mean_mag": np.linspace(8.0, 15.0, count),
            })


def _no_sleep(_seconds: float) -> None:
    """Skip the pause between requests, so tests run instantly."""


def test_pixel_source_id_ranges_tile_the_whole_id_space_without_gaps():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Neighbouring chunks meet exactly, so no star falls between two."""
    for level in (0, 3, 4):
        count = 12 * 4**level
        previous_high = 0
        for pixel in range(count):
            low, high = pixel_source_id_range(level, pixel)
            assert low == previous_high
            assert high > low
            previous_high = high
        # The end is 12 * 2**59, just above Gaia DR3's largest source ID.
        assert previous_high == 12 << 59
        assert previous_high > 6917528997577384320


def test_a_fine_pixel_lies_inside_its_coarse_parent():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A level 12 pixel p is inside level 4 pixel p >> 16."""
    fine_pixel = 123_456_789
    fine_low, fine_high = pixel_source_id_range(12, fine_pixel)
    coarse_low, coarse_high = pixel_source_id_range(4, fine_pixel >> 16)

    assert coarse_low <= fine_low < fine_high <= coarse_high
    assert fine_low == fine_pixel * 34359738368  # 2**35, as in Gaia's own documentation


@pytest.mark.parametrize(("level", "pixel"), [(-1, 0), (13, 0), (0, -1), (0, 12), (4, 3072)])
def test_pixel_source_id_range_rejects_out_of_range_arguments(level, pixel):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A typo must not silently ask for the wrong part of the sky."""
    with pytest.raises(ValueError, match=r"HEALPix level|Pixel"):
        pixel_source_id_range(level, pixel)


def test_query_asks_for_the_id_range_and_magnitude_limit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The query has the range, the depth, and the columns it needs."""
    query = build_pixel_query(100, 200, 16.0)

    assert "source_id >= 100 AND source_id < 200" in query
    assert "phot_g_mean_mag < 16.0" in query
    assert "source_id, ra, dec, phot_g_mean_mag" in query
    assert "COUNT" not in query
    assert "COUNT(*)" in build_pixel_query(100, 200, 16.0, count_only=True)


def test_full_download_saves_every_pixel_and_reports_it(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A clean run saves all 12 chunks and says so."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive(stars_per_pixel=5)

    report = build_deep_star_catalog(config, LEVEL, 16.0, gaia=archive, sleep=_no_sleep)

    assert report["pixels_downloaded"] == PIXEL_COUNT
    assert report["pixels_failed"] == []
    assert report["stars_added"] == PIXEL_COUNT * 5
    assert report["stopped_early"] is False
    status = deep_star_store.get_deep_catalog_status(config)
    assert status["complete"] is True
    assert status["star_count"] == PIXEL_COUNT * 5


def test_running_again_downloads_nothing_it_already_has(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A finished catalog is not downloaded a second time."""
    config = _LibraryConfig(tmp_path)
    build_deep_star_catalog(config, LEVEL, 16.0, gaia=_FakeArchive(), sleep=_no_sleep)
    second_archive = _FakeArchive()

    report = build_deep_star_catalog(config, LEVEL, 16.0, gaia=second_archive, sleep=_no_sleep)

    assert second_archive.queries == []
    assert report["pixels_previously_done"] == PIXEL_COUNT
    assert report["pixels_downloaded"] == 0


def test_failed_pixel_is_skipped_then_picked_up_on_the_next_run(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """One bad chunk does not stop the rest, and a rerun fetches only it."""
    config = _LibraryConfig(tmp_path)
    bad_start, _ = pixel_source_id_range(LEVEL, 3)
    flaky_archive = _FakeArchive(fail_range_starts={bad_start})

    first = build_deep_star_catalog(config, LEVEL, 16.0, max_attempts=2, gaia=flaky_archive, sleep=_no_sleep)

    assert first["pixels_failed"] == [3]
    assert first["pixels_downloaded"] == PIXEL_COUNT - 1
    assert flaky_archive.calls_by_range_start[bad_start] == 2  # tried twice, then skipped
    assert deep_star_store.get_deep_catalog_status(config)["complete"] is False

    healthy_archive = _FakeArchive()
    second = build_deep_star_catalog(config, LEVEL, 16.0, gaia=healthy_archive, sleep=_no_sleep)

    assert second["pixels_downloaded"] == 1
    assert len(healthy_archive.queries) == 1
    assert deep_star_store.get_deep_catalog_status(config)["complete"] is True


def test_a_transient_failure_is_retried_with_a_growing_wait(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A blip from the archive is retried, and the wait grows each time."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive()
    archive.fail_first_calls = 2
    sleeps: list[float] = []

    report = build_deep_star_catalog(
        config,
        LEVEL,
        16.0,
        request_delay_seconds=0.0,
        max_attempts=3,
        gaia=archive,
        sleep=sleeps.append,
        maximum_pixels=1,
    )

    assert report["pixels_downloaded"] == 1
    assert report["pixels_failed"] == []
    assert sleeps == [5.0, 10.0]


def test_run_stops_when_the_archive_keeps_failing(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Five chunks failing in a row means the archive is down; stop."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive(
        fail_range_starts={pixel_source_id_range(LEVEL, pixel)[0] for pixel in range(PIXEL_COUNT)}
    )

    report = build_deep_star_catalog(config, LEVEL, 16.0, max_attempts=1, gaia=archive, sleep=_no_sleep)

    assert report["stopped_early"] is True
    assert len(report["pixels_failed"]) == 5
    assert report["pixels_downloaded"] == 0
    assert deep_star_store.get_deep_catalog_status(config)["installed"] is False


def test_a_result_at_the_archive_row_limit_is_not_trusted(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A result that may have been cut short must not be saved as complete."""
    config = _LibraryConfig(tmp_path)
    monkeypatch.setattr(deep_catalog_builder, "_ANONYMOUS_ASYNC_ROW_LIMIT", 3)
    archive = _FakeArchive(stars_per_pixel=3)

    report = build_deep_star_catalog(config, LEVEL, 16.0, max_attempts=1, gaia=archive, sleep=_no_sleep)

    assert report["pixels_downloaded"] == 0
    assert deep_star_store.get_downloaded_pixels(config) == set()


def test_missing_result_table_is_a_failure(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """No table at all must not be saved as an empty chunk."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive()
    archive.return_none = True

    report = build_deep_star_catalog(
        config, LEVEL, 16.0, max_attempts=1, gaia=archive, sleep=_no_sleep, maximum_pixels=3
    )

    assert report["pixels_downloaded"] == 0
    assert deep_star_store.get_downloaded_pixels(config) == set()


def test_a_genuinely_empty_chunk_is_saved_as_finished(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A patch of sky with no stars this bright is still finished."""
    config = _LibraryConfig(tmp_path)

    report = build_deep_star_catalog(
        config, LEVEL, 16.0, gaia=_FakeArchive(stars_per_pixel=0), sleep=_no_sleep
    )

    assert report["pixels_downloaded"] == PIXEL_COUNT
    assert deep_star_store.get_deep_catalog_status(config)["complete"] is True
    assert deep_star_store.get_deep_catalog_status(config)["star_count"] == 0


def test_rows_with_missing_values_are_dropped_not_saved(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A star with no magnitude, or a NaN position, cannot be drawn."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive()
    archive.rows_override = Table({
        "source_id": [10, 11, 12, 13],
        "ra": [100.0, 100.1, float("nan"), 100.3],
        "dec": [10.0, 10.1, 10.2, 10.3],
        "phot_g_mean_mag": MaskedColumn([9.0, 0.0, 11.0, 12.0], mask=[False, True, False, False]),
    })

    build_deep_star_catalog(config, LEVEL, 16.0, gaia=archive, sleep=_no_sleep, maximum_pixels=1)

    stars = deep_star_store.find_deep_stars(config, 100.15, 10.15, 5.0, 16.0)
    assert sorted(star[0] for star in stars) == [10, 13]


def test_maximum_pixels_stops_a_short_trial_run(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A trial run of a few chunks stops when asked and can be resumed."""
    config = _LibraryConfig(tmp_path)

    report = build_deep_star_catalog(
        config, LEVEL, 16.0, gaia=_FakeArchive(), sleep=_no_sleep, maximum_pixels=4
    )

    assert report["pixels_downloaded"] == 4
    assert report["stopped_early"] is True
    assert deep_star_store.get_deep_catalog_status(config)["pixels_downloaded"] == 4


def test_catalog_started_with_other_settings_is_refused(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Chunks made at different depths cannot be mixed into one catalog."""
    config = _LibraryConfig(tmp_path)
    build_deep_star_catalog(config, LEVEL, 16.0, gaia=_FakeArchive(), sleep=_no_sleep, maximum_pixels=1)

    with pytest.raises(ValueError, match="magnitude_limit"):
        build_deep_star_catalog(config, LEVEL, 15.0, gaia=_FakeArchive(), sleep=_no_sleep)


def test_progress_callback_is_told_about_every_pixel_and_cannot_break_the_run(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The progress display hears each chunk; its errors are contained."""
    config = _LibraryConfig(tmp_path)
    heard: list[dict[str, Any]] = []

    def _record_then_fail(progress: dict[str, Any]) -> None:
        heard.append(progress)
        raise RuntimeError("the display broke")

    report = build_deep_star_catalog(
        config,
        LEVEL,
        16.0,
        gaia=_FakeArchive(stars_per_pixel=2),
        sleep=_no_sleep,
        progress_callback=_record_then_fail,
    )

    assert report["pixels_downloaded"] == PIXEL_COUNT
    assert len(heard) == PIXEL_COUNT
    assert heard[-1]["pixels_done"] == PIXEL_COUNT
    assert heard[-1]["stars_total"] == PIXEL_COUNT * 2


def test_request_delay_is_used_between_pixels_but_not_after_the_last(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The pause paces requests but never delays the end of the run."""
    config = _LibraryConfig(tmp_path)
    sleeps: list[float] = []

    build_deep_star_catalog(
        config, LEVEL, 16.0, request_delay_seconds=2.0, gaia=_FakeArchive(), sleep=sleeps.append
    )

    assert sleeps == [2.0] * (PIXEL_COUNT - 1)


def test_download_that_never_answers_is_abandoned_after_the_timeout(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A query stuck in the archive's queue must not hang the whole run."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive()
    archive.hang_event = threading.Event()

    try:
        report = build_deep_star_catalog(
            config,
            LEVEL,
            16.0,
            max_attempts=1,
            query_timeout_seconds=0.05,
            gaia=archive,
            sleep=_no_sleep,
            maximum_pixels=1,
        )
    finally:
        archive.hang_event.set()

    assert report["pixels_downloaded"] == 0
    assert report["pixels_failed"][0] == 0


def test_size_estimate_scales_a_sample_up_to_the_whole_sky():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The estimate is the sample's mean times the number of chunks."""
    archive = _FakeArchive(stars_per_pixel=1000)

    estimate = estimate_deep_catalog_size(LEVEL, 16.0, sample_count=6, gaia=archive, sleep=_no_sleep)

    assert estimate["pixels_total"] == PIXEL_COUNT
    assert estimate["pixels_sampled"] == 6
    assert estimate["estimated_stars"] == pytest.approx(12_000)
    assert estimate["estimated_megabytes"] == pytest.approx(
        12_000 * deep_catalog_builder.MEASURED_BYTES_PER_STAR / 1e6
    )
    assert all("COUNT(*)" in query for query in archive.queries)


def test_size_estimate_samples_are_spread_across_the_sky():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Evenly spaced samples touch every one of the twelve biggest patches."""
    archive = _FakeArchive()

    estimate_deep_catalog_size(4, 16.0, sample_count=24, gaia=archive, sleep=_no_sleep)

    starts = sorted(int(ID_RANGE_PATTERN.search(query).group(1)) for query in archive.queries)
    base_pixels = {start >> 59 for start in starts}
    assert base_pixels == set(range(12))


def test_size_estimate_uses_the_counts_that_worked():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A few failed counts are skipped rather than ruining the estimate."""
    archive = _FakeArchive(stars_per_pixel=500)
    archive.fail_first_calls = 2

    estimate = estimate_deep_catalog_size(LEVEL, 16.0, sample_count=6, gaia=archive, sleep=_no_sleep)

    assert estimate["pixels_sampled"] == 4
    assert estimate["estimated_stars"] == pytest.approx(500 * PIXEL_COUNT)


def test_size_estimate_with_no_working_counts_raises():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """With nothing counted there is nothing honest to report."""
    archive = _FakeArchive()
    archive.fail_first_calls = 100

    with pytest.raises(RuntimeError, match="nothing to estimate"):
        estimate_deep_catalog_size(LEVEL, 16.0, sample_count=3, gaia=archive, sleep=_no_sleep)


# Real Gaia DR3 stars from the local Gaia cache, as (source_id, ra, dec).
# Every Gaia ID contains the star's HEALPix pixel at level 12 (ID // 2**35),
# so each one is a known right answer for `healpix_pixels_of_points`. They
# were picked to sit away from pixel edges and to be spread over the part of
# the sky the cache covers (declination -9 to +90 degrees; the cache holds
# nothing further south).
REAL_GAIA_STARS = [
    (3216076459047432576, 84.157318, -3.011855),
    (3017256345539703680, 83.658174, -5.685763),
    (3131979556282532352, 97.748858, 5.60661),
    (598964495444498816, 132.063126, 11.697245),
    (66521797809401472, 57.019427, 23.986438),
    (381249579555539072, 9.854079, 41.498679),
    (1328057871370922368, 250.399995, 36.451084),
    (1551089132139645696, 200.986347, 46.186764),
    (1609407709912024448, 211.284872, 54.967845),
    (1089550636544676224, 114.284482, 65.111841),
    (1066790131669416960, 149.999907, 67.918022),
    (576317201612828800, 0.068464, 89.11556),
    (576459622729603584, 82.846606, 89.94583),
    (1729338108943926656, 180.623905, 89.41876),
]


@pytest.mark.parametrize("level", [0, 2, 4, 8, 12])
def test_healpix_pixel_of_a_real_gaia_star_matches_the_pixel_in_its_id(level):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The pixel worked out from a star's position is the one in its ID."""
    ids = np.array([star[0] for star in REAL_GAIA_STARS], dtype=np.int64)
    ra = np.array([star[1] for star in REAL_GAIA_STARS])
    dec = np.array([star[2] for star in REAL_GAIA_STARS])

    pixels = healpix_pixels_of_points(level, ra, dec)

    assert list(pixels) == list(ids >> (35 + 2 * (12 - level)))


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_healpix_pixels_all_have_the_same_area_over_the_whole_sky(level):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Every pixel of a level gets its fair share of evenly spread points.

    The real stars above only cover the northern sky, so this is what
    checks the southern half: a mistake there would leave some pixels with
    no points at all and give others too many. Points spread evenly over
    a sphere have a uniformly random sine of the declination.
    """
    random_generator = np.random.default_rng(seed=11)
    point_count = 400_000
    ra = random_generator.uniform(0.0, 360.0, point_count)
    dec = np.degrees(np.arcsin(random_generator.uniform(-1.0, 1.0, point_count)))

    pixels = healpix_pixels_of_points(level, ra, dec)

    pixel_count = 12 * 4**level
    counts = np.bincount(pixels, minlength=pixel_count)
    expected = point_count / pixel_count
    # Five standard deviations of a count of about `expected` points.
    assert len(counts) == pixel_count
    assert np.all(np.abs(counts - expected) < 5 * np.sqrt(expected))


def test_healpix_pixels_of_points_rejects_a_level_that_does_not_exist():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A level outside 0..12 raises instead of returning nonsense."""
    with pytest.raises(ValueError, match="level"):
        healpix_pixels_of_points(13, 10.0, 10.0)


CIRCLES = [
    (250.0, 36.0, 1.0),  # an ordinary spot
    (149.9, 68.0, 0.8),  # high declination
    (1.0, 10.0, 3.0),  # across the 0/360 degree seam
    (359.5, -20.0, 2.0),  # across the seam, in the south
    (30.0, 88.0, 4.0),  # around the north pole
    (200.0, -89.0, 3.0),  # around the south pole
    (100.0, -45.0, 10.0),  # a big southern circle
]


@pytest.mark.parametrize(("center_ra", "center_dec", "radius"), CIRCLES)
def test_every_point_inside_a_circle_is_in_a_chosen_pixel(center_ra, center_dec, radius):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """No star inside a circle can be left in a chunk that was not chosen."""
    random_generator = np.random.default_rng(seed=5)
    # Random distances and directions from the center, then turned into
    # positions with the standard spherical formulas.
    distance = np.radians(radius) * np.sqrt(random_generator.uniform(0.0, 1.0, 20_000))
    direction = random_generator.uniform(0.0, 2 * np.pi, 20_000)
    center_dec_radians = np.radians(center_dec)
    sin_dec = np.sin(center_dec_radians) * np.cos(distance) + np.cos(center_dec_radians) * np.sin(
        distance
    ) * np.cos(direction)
    dec = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
    ra = np.radians(center_ra) + np.arctan2(
        np.sin(direction) * np.sin(distance) * np.cos(center_dec_radians),
        np.cos(distance) - np.sin(center_dec_radians) * sin_dec,
    )

    chosen = set(pixels_near_circles(4, [(center_ra, center_dec, radius)]))
    needed = set(healpix_pixels_of_points(4, np.degrees(ra), np.degrees(dec)).tolist())

    assert needed <= chosen


def test_a_small_circle_chooses_only_a_few_pixels():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A field-sized circle needs a few of the 3072 chunks, not thousands."""
    chosen = pixels_near_circles(4, [(250.0, 36.0, 0.8)])

    assert 1 <= len(chosen) <= 4
    assert chosen == sorted(set(chosen))


def test_the_pixel_at_a_circles_center_is_always_chosen():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The center's own chunk is in the list, including at the poles."""
    for center_ra, center_dec, radius in CIRCLES:
        center_pixel = int(healpix_pixels_of_points(4, center_ra, center_dec))
        assert center_pixel in pixels_near_circles(4, [(center_ra, center_dec, radius)])


def test_several_circles_choose_the_union_of_their_pixels():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Two far-apart circles give both circles' chunks, each listed once."""
    first = pixels_near_circles(4, [(250.0, 36.0, 0.8)])
    second = pixels_near_circles(4, [(10.0, -50.0, 0.8)])

    both = pixels_near_circles(4, [(250.0, 36.0, 0.8), (10.0, -50.0, 0.8), (250.0, 36.0, 0.8)])

    assert both == sorted(set(first) | set(second))


def test_a_circle_as_big_as_the_sky_chooses_every_pixel():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Whole-sky radius selects all chunks."""
    assert pixels_near_circles(1, [(0.0, 0.0, 180.0)]) == list(range(48))


def test_building_only_the_chosen_pixels_leaves_the_rest_alone(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Only the asked-for chunks are requested, and the report counts them."""
    config = _LibraryConfig(tmp_path)
    archive = _FakeArchive(stars_per_pixel=5)

    report = build_deep_star_catalog(config, LEVEL, 16.0, gaia=archive, sleep=_no_sleep, pixels=[7, 2, 7])

    assert len(archive.queries) == 2
    assert report["pixels_total"] == 2
    assert report["pixels_downloaded"] == 2
    assert deep_star_store.get_downloaded_pixels(config) == {2, 7}
    status = deep_star_store.get_deep_catalog_status(config)
    assert status["installed"] is True
    assert status["complete"] is False


def test_a_later_run_for_more_pixels_skips_the_ones_already_saved(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A second, wider selection only fetches what is new."""
    config = _LibraryConfig(tmp_path)
    build_deep_star_catalog(config, LEVEL, 16.0, gaia=_FakeArchive(), sleep=_no_sleep, pixels=[2, 7])
    second_archive = _FakeArchive()

    report = build_deep_star_catalog(
        config, LEVEL, 16.0, gaia=second_archive, sleep=_no_sleep, pixels=[2, 7, 9]
    )

    assert len(second_archive.queries) == 1
    assert report["pixels_previously_done"] == 2
    assert report["pixels_total"] == 3
    assert deep_star_store.get_downloaded_pixels(config) == {2, 7, 9}


def test_progress_counts_only_the_chosen_pixels(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The 'done of total' shown to the user is about the chosen chunks."""
    config = _LibraryConfig(tmp_path)
    seen = []

    build_deep_star_catalog(
        config,
        LEVEL,
        16.0,
        gaia=_FakeArchive(),
        sleep=_no_sleep,
        pixels=[1, 4, 8],
        progress_callback=seen.append,
    )

    assert [(item["pixels_done"], item["pixels_total"]) for item in seen] == [(1, 3), (2, 3), (3, 3)]


def test_asking_for_a_pixel_outside_the_sky_is_refused_before_anything_is_saved(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A bad chunk number raises and leaves no catalog file behind."""
    config = _LibraryConfig(tmp_path)

    with pytest.raises(ValueError, match="outside"):
        build_deep_star_catalog(
            config, LEVEL, 16.0, gaia=_FakeArchive(), sleep=_no_sleep, pixels=[3, PIXEL_COUNT]
        )

    assert not deep_star_store.get_deep_catalog_path(config).exists()
