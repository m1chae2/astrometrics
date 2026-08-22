"""Execution queue and control loop for target imaging sequences."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class TargetImagingExecutor:
    """Manage the execution queue and drive the telescope control loop.

    REQ: BKD-2: Imaging Execution REQ: SR-2.1: The system SHALL capture
    astronomical images via connected cameras.
    """

    def __init__(self, telescope_service=None, imaging_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._queue: list[dict[str, Any]] = []
        self._is_running = False
        self._telescope_service = telescope_service
        self._imaging_service = imaging_service
        self._background_tasks: set = set()

    def enqueue_sequence(self, sequence: dict[str, Any], timing: dict[str, Any]) -> dict[str, str]:
        """RPC wrapper conforming to add_to_queue signature; returns status.

        Returns
        -------
        result : `dict`
            Dict with ``"status"`` set to ``"queued"``.
        """
        self.add_to_queue(sequence, timing)
        return {"status": "queued"}

    def add_to_queue(self, sequence: dict[str, Any], timing_point: dict[str, Any]) -> None:
        """Add a sequence to the execution queue.

        REQ: BKD-2.1: The backend SHALL accept and queue imaging
        sequences containing multiple frames.
        """
        sequence["timing"] = timing_point
        sequence["status"] = "queued"
        self._queue.append(sequence)
        logger.info(f"Added sequence {sequence['id']} to execution queue. Total queued: {len(self._queue)}")

    def remove_from_queue(self, sequence_id: str) -> bool:
        """Remove a sequence from the queue by ID.

        Returns
        -------
        removed : `bool`
            `True` if a sequence with `sequence_id` was found and
            removed, `False` otherwise.
        """
        initial_len = len(self._queue)
        self._queue = [s for s in self._queue if s["id"] != sequence_id]
        removed = len(self._queue) < initial_len
        if removed:
            logger.info(f"Removed sequence {sequence_id} from queue.")
        return removed

    def modify_queue_item(self, sequence_id: str, sequence: dict[str, Any]) -> bool:
        """Modify an existing sequence in the queue.

        Retains the item's original position in the queue.

        Returns
        -------
        modified : `bool`
            `True` if a sequence with `sequence_id` was found and
            replaced, `False` otherwise.
        """
        for i, item in enumerate(self._queue):
            if item["id"] == sequence_id:
                # Ensure the ID stays the same
                sequence["id"] = sequence_id
                # Inherit status and timing if not overridden
                if "status" not in sequence:
                    sequence["status"] = item.get("status", "queued")
                if "timing" not in sequence:
                    sequence["timing"] = item.get("timing", {"mode": "soonest"})

                self._queue[i] = sequence
                logger.info(f"Modified sequence {sequence_id} in queue.")
                return True
        return False

    def reorder_queue(self, sequence_ids: list[str]) -> bool:
        """Reorder the execution queue to match the given sequence IDs.

        Returns
        -------
        reordered : `bool`
            `True` once the queue has been reordered; `False` if
            `sequence_ids` is empty and no reordering was performed.
        """
        if not sequence_ids:
            return False

        current_map = {item["id"]: item for item in self._queue}

        new_queue = []
        seen_ids = set()

        for seq_id in sequence_ids:
            if seq_id in current_map:
                new_queue.append(current_map[seq_id])
                seen_ids.add(seq_id)

        # Append any items not in the input list (safety)
        for item in self._queue:
            sid = item["id"]
            if sid not in seen_ids:
                new_queue.append(item)

        self._queue = new_queue
        logger.info(f"Reordered execution queue. New sequence: {[item['id'] for item in self._queue]}")
        return True

    def get_queue(self) -> list[dict[str, Any]]:
        """Return the current execution queue.

        Returns
        -------
        queue : `list` [`dict`]
            The list of currently queued imaging sequences.
        """
        return self._queue

    async def begin_imaging(self) -> None:
        """Start the imaging process by running the queue in background."""
        if not self._queue:
            logger.warning("Attempted to begin imaging with empty queue.")
            return

        if self._is_running:
            logger.warning("Imaging already in progress.")
            return

        self._is_running = True
        logger.info("BEGIN IMAGING SEQUENCE initiated.")
        task = asyncio.create_task(self._run_queue())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_queue(self):  # ruff: ignore[missing-return-type-private-function]
        """Process the queue in an internal loop."""
        if not self._telescope_service or not self._imaging_service:
            logger.error("Missing required services (telescope/imaging) in TargetImagingExecutor")
            self._is_running = False
            return

        try:
            while self._queue and self._is_running:
                # 1. Get Next Item
                seq = self._queue[0]  # Peek

                # Check status
                if seq.get("status") == "completed":
                    self._queue.pop(0)
                    continue

                logger.info(f"Processing sequence {seq['id']} for {seq['target_name']}")
                seq["status"] = "active"

                # 2. Slew to Target
                target = seq.get("target", {})
                ra = target.get("ra")
                dec = target.get("dec")

                if ra and dec:
                    logger.info(f"Slewing to RA: {ra}, DEC: {dec}")
                    # Simulate Slew Wait
                    await asyncio.sleep(5)

                # 3. Operations (Filter, Exposure)
                # seq['plan'] is list of {filter: 'L', exposure: 60, count: 10}
                plan = seq.get("plan", [])

                for step in plan:
                    if not self._is_running:
                        break

                    filter_name = step.get("filter")
                    exposure = float(step.get("exposure", 1))
                    count = int(step.get("count", 1))

                    # Set Filter
                    if filter_name:
                        logger.info(f"Setting Filter: {filter_name}")
                        self._telescope_service.set_filter(filter_name)
                        await asyncio.sleep(3)  # Wait for wheel

                    # Capture Sequence
                    # Initiate background job and wait for its
                    # completion via JobService
                    job_id = await self._imaging_service.capture_sequence(
                        target_id=seq["target_name"],
                        exposure_seconds=exposure,
                        count=count,
                        image_type=step.get("type", "LIGHT"),
                    )

                    # Wait for job to complete

                    job = await self._imaging_service.job_service.wait_for_job(job_id)

                    if job.status == "failed":
                        logger.error(f"Capture sequence failed for {seq['target_name']}: {job.message}")
                        break

                    logger.info(f"Capture sequence completed for {seq['target_name']}")

                # 4. Mark Complete
                if self._is_running:
                    seq["status"] = "completed"
                    logger.info(f"Sequence {seq['id']} completed.")
                    self._queue.pop(0)

        except Exception as e:
            logger.error(f"Imaging Loop Crashed: {e}", exc_info=True)
        finally:
            self._is_running = False
            logger.info("Imaging Loop Ended.")

    def pause_imaging(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Stop the run loop after the current step; keep the queue intact."""
        self._is_running = False
        logger.info("Imaging Paused.")

    def abort_imaging(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Stop the run loop and clear the execution queue."""
        self._is_running = False
        self._queue = []
        logger.info("Imaging Aborted.")
