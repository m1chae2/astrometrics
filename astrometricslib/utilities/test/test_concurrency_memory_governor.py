"""Purpose: Unit tests for the memory-aware concurrency governor.

Description: Verifies that resolve_worker_counts dynamically constrains
concurrent worker counts based on available system memory in addition to
CPU core counts, preventing batch workloads from triggering out-of-memory
crashes under heavy memory pressure.
"""

from typing import Any

from astrometricslib.utilities.concurrency import WorkerCounts, resolve_worker_counts


class TestConcurrencyMemoryGovernor:
    """Test suite for memory-aware worker count resolution."""

    def test_auto_worker_scaling_under_ample_memory(self) -> None:
        """Worker count reaches CPU-bound maximum when memory is abundant."""
        # 64 GB total, 48 GB available, 12 CPUs
        total_ram = 64 * 1024 * 1024 * 1024
        available_ram = 48 * 1024 * 1024 * 1024

        counts = resolve_worker_counts(
            outer_worker_setting="1",
            inner_worker_setting="auto",
            cpu_count=12,
            available_ram_bytes=available_ram,
            total_ram_bytes=total_ram,
            estimated_mb_per_worker=1536,
        )

        assert isinstance(counts, WorkerCounts)
        assert counts.outer_worker_count == 1
        # 12 cores - 1 OS core = 11 usable cores
        assert counts.inner_worker_count == 11

    def test_auto_worker_scaling_under_restricted_memory(self) -> None:
        """Worker count dynamically clamps down when memory is scarce."""
        # 16 GB total, only 4 GB available, 12 CPUs
        total_ram = 16 * 1024 * 1024 * 1024
        available_ram = 4 * 1024 * 1024 * 1024

        counts = resolve_worker_counts(
            outer_worker_setting="1",
            inner_worker_setting="auto",
            cpu_count=12,
            available_ram_bytes=available_ram,
            total_ram_bytes=total_ram,
            estimated_mb_per_worker=1536,
        )

        assert counts.outer_worker_count == 1
        # Usable memory after safety headroom (3276 MB) is only ~820 MB,
        # which safely clamps inner workers to 1 instead of 11.
        assert counts.inner_worker_count == 1

    def test_auto_worker_scaling_under_medium_memory(self) -> None:
        """Worker count scales with moderate available memory."""
        # 16 GB total, 10 GB available, 12 CPUs
        total_ram = 16 * 1024 * 1024 * 1024
        available_ram = 10 * 1024 * 1024 * 1024

        counts = resolve_worker_counts(
            outer_worker_setting="1",
            inner_worker_setting="auto",
            cpu_count=12,
            available_ram_bytes=available_ram,
            total_ram_bytes=total_ram,
            estimated_mb_per_worker=1536,
        )

        assert counts.outer_worker_count == 1
        # Usable RAM: 10240 MB - 3276 MB = 6964 MB; 6964 // 1536 = 4 workers
        assert counts.inner_worker_count == 4

    def test_explicit_worker_setting_clamped_when_exceeding_safe_memory(self, caplog: Any) -> None:
        """Explicit worker count is safely capped if memory would exhaust."""
        # 16 GB total, only 4 GB available (allows 1 worker safely)
        total_ram = 16 * 1024 * 1024 * 1024
        available_ram = 4 * 1024 * 1024 * 1024

        counts = resolve_worker_counts(
            outer_worker_setting="1",
            inner_worker_setting="10",
            cpu_count=12,
            available_ram_bytes=available_ram,
            total_ram_bytes=total_ram,
            estimated_mb_per_worker=1536,
        )

        assert counts.outer_worker_count == 1
        assert counts.inner_worker_count == 1
        assert "exceeds safe system memory capacity" in caplog.text

    def test_auto_outer_workers_always_resolves_to_one_target_at_a_time(self) -> None:
        """The "auto" outer-worker setting always means one target at a time.

        A single target's own pipeline (stacking, astrometry,
        photometry, spectroscopy) is memory-heavy enough on its own --
        a real production run needed well over the old default
        per-worker memory ceiling just for one target's stacking step
        -- that running several targets concurrently is a reliability
        risk this application no longer takes, regardless of how many
        CPU cores are available. This also verifies "auto" inner
        workers then scales to use every usable core for that one
        active target, rather than splitting cores across targets that
        no longer run concurrently.
        """
        total_ram = 64 * 1024 * 1024 * 1024
        available_ram = 48 * 1024 * 1024 * 1024

        counts = resolve_worker_counts(
            outer_worker_setting="auto",
            inner_worker_setting="auto",
            cpu_count=12,
            available_ram_bytes=available_ram,
            total_ram_bytes=total_ram,
            estimated_mb_per_worker=1536,
        )

        assert counts.outer_worker_count == 1
        # 12 cores - 1 OS core = 11 usable cores, all given to the one
        # active target instead of being split three ways.
        assert counts.inner_worker_count == 11

    def test_live_system_resolution_runs_without_error(self) -> None:
        """Querying live system resources via psutil executes safely."""
        counts = resolve_worker_counts(
            outer_worker_setting="1",
            inner_worker_setting="auto",
        )

        assert isinstance(counts, WorkerCounts)
        assert counts.outer_worker_count >= 1
        assert counts.inner_worker_count >= 1
